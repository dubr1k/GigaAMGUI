"""Issue #42: stopping a session must not discard undecoded speech."""

import time

import numpy as np

from src.live.asr import LiveAsrScheduler
from src.live.exports import ExportSelection
from src.live.session import LiveSession
from src.live.types import CaptureSource, LiveSettings, PcmChunk


class SlowBackend:
    """First decode is slow, the way a cold model load is on a portable build."""

    def __init__(self, delay: float = 1.5):
        self._delay = delay
        self.calls = 0

    def transcribe_window(self, audio, sample_rate, offset_samples):
        self.calls += 1
        if self.calls == 1:
            time.sleep(self._delay)
        return [{"transcription": "recognized speech here", "boundaries": (0, 1)}]


class FakeAdapter:
    def start(self, on_chunk, on_event):
        self.on_chunk = on_chunk
        self.on_event = on_event

    def pause(self):
        return None

    def resume(self):
        return None

    def stop(self):
        return None


def voiced_chunk(offset: int, seconds: float, source=CaptureSource.MIC) -> PcmChunk:
    frames = np.ones((int(seconds * 16_000), 1), dtype=np.float32)
    return PcmChunk(source, 16_000, 1, offset, frames, 1)


def test_close_drains_queued_finals_even_when_a_decode_outlives_one_second():
    backend = SlowBackend(delay=1.4)
    finals = []
    scheduler = LiveAsrScheduler(backend, on_final=finals.append)

    scheduler.submit(voiced_chunk(0, 3))
    scheduler.close()

    assert finals, "queued final work was abandoned by close()"


def test_close_honours_an_explicit_drain_timeout():
    backend = SlowBackend(delay=5)
    scheduler = LiveAsrScheduler(backend, on_final=lambda event: None)

    scheduler.submit(voiced_chunk(0, 3))
    started = time.monotonic()
    scheduler.close(timeout=0.2)

    assert time.monotonic() - started < 2


def test_stop_exports_after_the_last_final_lands(tmp_path):
    adapter = FakeAdapter()
    backend = SlowBackend(delay=1.2)
    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False, record_source_audio=False),
        {CaptureSource.MIC: adapter},
        scheduler_factory=lambda source, on_final, on_partial, on_error: LiveAsrScheduler(
            backend, on_final=on_final, on_partial=on_partial, on_error=on_error
        ),
        export_selection=ExportSelection(txt=True),
    )
    session.start()
    adapter.on_chunk(voiced_chunk(0, 3))

    result = session.stop()

    exported = [path for path in result.exports if path.suffix == ".txt"]
    assert exported, "no transcript export was produced"
    assert "recognized speech here" in exported[0].read_text(encoding="utf-8")


def test_stop_reports_stopping_before_it_drains(tmp_path):
    adapter = FakeAdapter()
    states = []
    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False, record_source_audio=False),
        {CaptureSource.MIC: adapter},
        scheduler_factory=lambda source, on_final, on_partial, on_error: LiveAsrScheduler(
            SlowBackend(delay=0), on_final=on_final, on_partial=on_partial, on_error=on_error
        ),
    )
    session.subscribe(lambda value: states.append(getattr(value, "state", None)))
    session.start()

    session.stop()

    assert "stopping" in [state.value for state in states if state is not None]


def test_short_utterance_still_produces_a_final(tmp_path):
    """A 1.2s phrase never reaches the partial cadence but is real speech."""
    backend = SlowBackend(delay=0)
    finals = []
    scheduler = LiveAsrScheduler(backend, on_final=finals.append)
    try:
        scheduler.submit(voiced_chunk(0, 1.2))
        scheduler.close()
    finally:
        pass

    assert [event.text for event in finals] == ["recognized speech here"]


def test_checkpoint_is_not_written_for_every_chunk(tmp_path, monkeypatch):
    writes = []

    def record_checkpoint(self, session_dir, checkpoint):
        writes.append(checkpoint)

    monkeypatch.setattr(
        "src.live.journal.LiveSessionStore.write_checkpoint", record_checkpoint
    )

    class NullScheduler:
        def submit(self, chunk):
            return None

        def flush(self):
            return None

        def close(self, timeout=None):
            return None

    adapter = FakeAdapter()
    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False, record_source_audio=False),
        {CaptureSource.MIC: adapter},
        scheduler_factory=lambda source, on_final, on_partial, on_error: NullScheduler(),
    )
    session.start()
    for index in range(50):
        adapter.on_chunk(voiced_chunk(index * 1_600, 0.1))

    assert len(writes) <= 2
