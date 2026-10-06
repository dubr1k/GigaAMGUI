"""stop() must always finish: stop every adapter, drain, close, export, end STOPPED."""

import threading

import numpy as np
import pytest

from src.live.asr import LiveAsrScheduler
from src.live.exports import ExportSelection
from src.live.recorder import SessionRecorder
from src.live.session import LiveSession
from src.live.types import (
    CaptureEvent,
    CaptureEventKind,
    CaptureSource,
    CaptureState,
    LiveSettings,
    PcmChunk,
    TranscriptEvent,
)


class FakeAdapter:
    def __init__(self, stop_error: Exception | None = None):
        self.stop_error = stop_error
        self.stop_calls = 0

    def start(self, on_chunk, on_event):
        self.on_chunk = on_chunk
        self.on_event = on_event

    def pause(self):
        return None

    def resume(self):
        return None

    def stop(self):
        self.stop_calls += 1
        if self.stop_error is not None:
            raise self.stop_error

    def fail(self, source, kind=CaptureEventKind.DEVICE_REMOVED):
        self.on_event(CaptureEvent(kind, source, 0, 1, "removed"))


class FakeScheduler:
    def __init__(self, on_final, *, close_error=None, flush_error=None):
        self._on_final = on_final
        self.close_error = close_error
        self.flush_error = flush_error
        self.flushed = False
        self.closed = False

    def submit(self, chunk):
        return None

    def flush(self):
        self.flushed = True
        if self.flush_error is not None:
            raise self.flush_error

    def close(self):
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class FakeRecorder:
    def __init__(self, *args, close_error=None):
        self.closed = False
        self.close_error = close_error

    def write(self, chunk):
        return None

    def write_mix(self, chunk):
        return None

    def close(self):
        self.closed = True
        if self.close_error is not None:
            raise self.close_error
        return {}


def final(source=CaptureSource.MIC, text="hello"):
    return TranscriptEvent(f"{source.value}-0", 0, source, 0, 16_000, 1, text, "final")


def build(tmp_path, adapters, *, recorder=None, scheduler_options=None):
    made = {}
    options = scheduler_options or {}

    def factory(source, on_final, on_partial, on_error):
        made[source] = FakeScheduler(on_final, **options.get(source, {}))
        return made[source]

    kwargs = {}
    if recorder is not None:
        kwargs["recorder_factory"] = lambda *args, **_: recorder
    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False),
        adapters,
        scheduler_factory=factory,
        export_selection=ExportSelection(txt=True),
        **kwargs,
    )
    return session, made


def test_stop_also_stops_a_source_that_failed_while_recording(tmp_path):
    """A failed source kept its dispatch thread and native stream (SCStream indicator)."""
    mic, system = FakeAdapter(), FakeAdapter()
    session, _ = build(tmp_path, {CaptureSource.MIC: mic, CaptureSource.SYSTEM: system})
    session.start()
    system.fail(CaptureSource.SYSTEM)

    session.stop()

    assert mic.stop_calls == 1
    assert system.stop_calls == 1


def test_stop_also_stops_a_source_whose_start_raised(tmp_path):
    class BrokenStart(FakeAdapter):
        def start(self, on_chunk, on_event):
            super().start(on_chunk, on_event)
            raise OSError("device busy")

    mic, system = FakeAdapter(), BrokenStart()
    session, _ = build(tmp_path, {CaptureSource.MIC: mic, CaptureSource.SYSTEM: system})
    session.start()

    session.stop()

    assert system.stop_calls == 1


def test_failing_adapter_stop_does_not_abort_drain_close_or_export(tmp_path):
    mic, system = FakeAdapter(stop_error=RuntimeError("stream gone")), FakeAdapter()
    recorder = FakeRecorder()
    session, schedulers = build(
        tmp_path, {CaptureSource.MIC: mic, CaptureSource.SYSTEM: system}, recorder=recorder,
    )
    session.start()
    session._on_final(final())

    result = session.stop()

    assert system.stop_calls == 1
    assert all(scheduler.flushed and scheduler.closed for scheduler in schedulers.values())
    assert recorder.closed
    assert (result.session_dir / "transcript.txt").read_text(encoding="utf-8") == "hello\n"
    assert session.status().state is CaptureState.STOPPED
    assert any("stream gone" in error for error in result.errors)


def test_failing_scheduler_close_still_closes_recorder_and_exports(tmp_path):
    mic = FakeAdapter()
    recorder = FakeRecorder()
    session, schedulers = build(
        tmp_path, {CaptureSource.MIC: mic}, recorder=recorder,
        scheduler_options={CaptureSource.MIC: {"close_error": RuntimeError("worker crashed")}},
    )
    session.start()
    session._on_final(final())

    result = session.stop()

    assert recorder.closed
    assert [path.name for path in result.exports] == ["transcript.txt"]
    assert session.status().state is CaptureState.STOPPED
    assert any("worker crashed" in error for error in result.errors)


def test_failing_recorder_close_still_exports_and_reports(tmp_path):
    mic = FakeAdapter()
    recorder = FakeRecorder(close_error=OSError("disk full"))
    events = []
    session, _ = build(tmp_path, {CaptureSource.MIC: mic}, recorder=recorder)
    session.subscribe(events.append)
    session.start()
    session._on_final(final())

    result = session.stop()

    assert [path.name for path in result.exports] == ["transcript.txt"]
    assert session.status().state is CaptureState.STOPPED
    assert any("disk full" in error for error in result.errors)
    assert any(isinstance(event, CaptureEvent) and "disk full" in event.detail for event in events)


def test_failing_export_still_ends_stopped(tmp_path, monkeypatch):
    def broken_export(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr("src.live.session.export_session", broken_export)
    mic = FakeAdapter()
    recorder = FakeRecorder()
    session, _ = build(tmp_path, {CaptureSource.MIC: mic}, recorder=recorder)
    session.start()

    result = session.stop()

    assert recorder.closed
    assert result.exports == []
    assert session.status().state is CaptureState.STOPPED
    assert any("read-only" in error for error in result.errors)


def test_failing_metadata_update_still_exports(tmp_path, monkeypatch):
    def broken_update(self, session_dir, **values):
        raise OSError("metadata locked")

    monkeypatch.setattr("src.live.journal.LiveSessionStore.update_metadata", broken_update)
    mic = FakeAdapter()
    session, _ = build(tmp_path, {CaptureSource.MIC: mic})
    session.start()
    session._on_final(final())

    result = session.stop()

    assert [path.name for path in result.exports] == ["transcript.txt"]
    assert any("metadata locked" in error for error in result.errors)


def test_clean_stop_reports_no_errors(tmp_path):
    session, _ = build(tmp_path, {CaptureSource.MIC: FakeAdapter()})
    session.start()

    assert session.stop().errors == []


def test_recorder_closes_every_writer_even_when_one_close_fails(tmp_path):
    writers = []

    class Writer:
        def __init__(self, path, **kwargs):
            self.path = path
            self.closed = False
            writers.append(self)

        def write(self, frames):
            return None

        def close(self):
            self.closed = True
            if len(writers) > 1 and self is writers[0]:
                raise OSError("flush failed")

    recorder = SessionRecorder(tmp_path, record_sources=True, record_mix=False, writer_factory=Writer)
    for source in (CaptureSource.MIC, CaptureSource.SYSTEM):
        recorder.write(PcmChunk(source, 48_000, 1, 0, np.ones((4, 1), np.float32), 1))

    with pytest.raises(OSError, match="flush failed") as raised:
        recorder.close()

    assert all(writer.closed for writer in writers)
    assert raised.value.recordings == {
        CaptureSource.MIC: tmp_path / "mic.flac",
        CaptureSource.SYSTEM: tmp_path / "system.flac",
    }


def test_late_device_event_after_stop_does_not_reopen_the_session(tmp_path):
    """A late DEVICE_REMOVED turned STOPPED into FAILED and allowed a second stop/export."""
    mic = FakeAdapter()
    session, _ = build(tmp_path, {CaptureSource.MIC: mic})
    session.start()
    session.stop()

    mic.fail(CaptureSource.MIC)

    assert session.status().state is CaptureState.STOPPED
    with pytest.raises(RuntimeError, match="not running"):
        session.stop()


def test_device_event_while_stopping_does_not_mark_the_session_failed(tmp_path):
    class ClosingAdapter(FakeAdapter):
        def stop(self):
            super().stop()
            self.fail(CaptureSource.MIC)

    session, _ = build(tmp_path, {CaptureSource.MIC: ClosingAdapter()})
    session.start()

    session.stop()

    assert session.status().state is CaptureState.STOPPED
    assert session.status().failed_sources == set()


def test_second_stop_while_draining_is_rejected(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    class SlowScheduler(FakeScheduler):
        def close(self):
            entered.set()
            release.wait(5)
            super().close()

    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False),
        {CaptureSource.MIC: FakeAdapter()},
        scheduler_factory=lambda source, on_final, on_partial, on_error: SlowScheduler(on_final),
    )
    session.start()
    first = threading.Thread(target=session.stop)
    first.start()
    assert entered.wait(5)
    try:
        with pytest.raises(RuntimeError, match="not running"):
            session.stop()
    finally:
        release.set()
        first.join(5)
    assert session.status().state is CaptureState.STOPPED


def test_stop_abandons_a_drain_that_outlives_its_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr("src.live.session.STOP_DRAIN_TIMEOUT_SECONDS", 0.2)
    started = threading.Event()
    release = threading.Event()

    class StuckBackend:
        def transcribe_window(self, audio, sample_rate, offset_samples):
            started.set()
            release.wait(10)
            return [{"transcription": "too late", "boundaries": (0, 1)}]

    adapter = FakeAdapter()
    finals = []
    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False, record_source_audio=False),
        {CaptureSource.MIC: adapter},
        scheduler_factory=lambda source, on_final, on_partial, on_error: LiveAsrScheduler(
            StuckBackend(), on_final=on_final, on_partial=on_partial, on_error=on_error,
        ),
        export_selection=ExportSelection(txt=True),
    )
    session.subscribe(lambda value: finals.append(value) if isinstance(value, TranscriptEvent) else None)
    session.start()
    adapter.on_chunk(PcmChunk(CaptureSource.MIC, 16_000, 1, 0, np.ones((48_000, 1), np.float32), 1))
    try:
        result = session.stop()
    finally:
        release.set()

    assert started.is_set()
    assert session.status().state is CaptureState.STOPPED
    assert any("did not finish" in error for error in result.errors)
    assert finals == []


def test_scheduler_close_reports_whether_it_drained():
    release = threading.Event()

    class BlockingBackend:
        def transcribe_window(self, audio, sample_rate, offset_samples):
            release.wait(5)
            return []

    scheduler = LiveAsrScheduler(BlockingBackend())
    scheduler.submit(PcmChunk(CaptureSource.MIC, 16_000, 1, 0, np.ones((48_000, 1), np.float32), 1))
    try:
        assert scheduler.close(timeout=0.1) is False
    finally:
        scheduler.abort()
        release.set()
    assert LiveAsrScheduler(BlockingBackend()).close() is True
