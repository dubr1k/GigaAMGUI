import time

import numpy as np

from src.live.capture.common import QueuedCaptureAdapter
from src.live.diagnostics import SessionLog
from src.live.session import LiveSession
from src.live.types import CaptureEvent, CaptureEventKind, CaptureSource, LiveSettings, PcmChunk


class SilentApi:
    def __init__(self):
        self.callback = None

    def devices(self, source):
        return []

    def start(self, source, device_id, callback):
        self.callback = callback

    def pause(self):
        return None

    def resume(self):
        return None

    def stop(self):
        return None


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


class FakeScheduler:
    def __init__(self, on_error=None):
        self._on_error = on_error

    def submit(self, chunk):
        return None

    def flush(self):
        return None

    def close(self):
        return None

    def fail(self, error):
        self._on_error(error)


def wait_until(predicate, timeout=1.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def test_chunk_consumer_failure_does_not_kill_capture_dispatch():
    api = SilentApi()
    adapter = QueuedCaptureAdapter(CaptureSource.MIC, api)
    seen = []
    events = []

    def consume(chunk):
        seen.append(chunk)
        raise RuntimeError("simulated recorder failure")

    adapter.start(consume, events.append)
    api.callback(np.ones((4, 1), dtype=np.float32), 1)
    wait_until(lambda: len(seen) == 1)
    api.callback(np.ones((4, 1), dtype=np.float32), 2)
    wait_until(lambda: len(seen) == 2)
    adapter.stop()

    assert len(seen) == 2
    assert any("simulated recorder failure" in event.detail for event in events)


def test_chunk_consumer_failure_is_reported_once_per_reason():
    api = SilentApi()
    adapter = QueuedCaptureAdapter(CaptureSource.MIC, api)
    events = []

    def consume(chunk):
        raise RuntimeError("same failure")

    adapter.start(consume, events.append)
    for index in range(5):
        api.callback(np.ones((4, 1), dtype=np.float32), index + 1)
    time.sleep(0.2)
    adapter.stop()

    failures = [event for event in events if "same failure" in event.detail]
    assert len(failures) == 1
    assert failures[0].kind is CaptureEventKind.STATUS


def test_event_consumer_failure_does_not_kill_capture_dispatch():
    api = SilentApi()
    adapter = QueuedCaptureAdapter(CaptureSource.MIC, api)
    chunks = []
    delivered = []

    def on_event(event):
        delivered.append(event)
        raise RuntimeError("subscriber exploded")

    adapter.start(chunks.append, on_event)
    adapter._emit(CaptureEventKind.STATUS, "first")
    wait_until(lambda: delivered)
    api.callback(np.ones((4, 1), dtype=np.float32), 1)
    wait_until(lambda: chunks)
    adapter.stop()

    assert chunks


def test_session_log_writes_lines_and_survives_write_errors(tmp_path):
    log = SessionLog(tmp_path / "live.log")
    log.write("capture started")
    log.write("mix disabled")

    text = (tmp_path / "live.log").read_text(encoding="utf-8")
    assert "capture started" in text
    assert "mix disabled" in text

    broken = SessionLog(tmp_path / "missing-dir-removed" / "live.log")
    broken._path = tmp_path  # a directory can never be opened for append
    broken.write("must not raise")


def test_session_logs_capture_events_and_asr_errors(tmp_path):
    adapter = FakeAdapter()
    scheduler = None
    lines = []

    def scheduler_factory(source, on_final, on_partial, on_error):
        nonlocal scheduler
        scheduler = FakeScheduler(on_error)
        return scheduler

    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False),
        {CaptureSource.MIC: adapter},
        scheduler_factory=scheduler_factory,
        log=lines.append,
    )
    session.start()
    adapter.on_event(CaptureEvent(CaptureEventKind.OVERFLOW, CaptureSource.MIC, 0, 1, "queue full"))
    scheduler.fail(RuntimeError("model missing"))

    assert any("queue full" in line for line in lines)
    assert any("model missing" in line for line in lines)
    log_text = (session.session_dir / "live.log").read_text(encoding="utf-8")
    assert "queue full" in log_text
    assert "model missing" in log_text


def test_session_logs_dropped_audio_once_per_source(tmp_path):
    adapter = FakeAdapter()
    lines = []
    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False),
        {CaptureSource.MIC: adapter},
        scheduler_factory=lambda source, on_final, on_partial, on_error: FakeScheduler(),
        log=lines.append,
    )
    session.start()
    for index in range(3):
        adapter.on_event(
            CaptureEvent(CaptureEventKind.OVERFLOW, CaptureSource.MIC, 0, index, "queue full")
        )

    assert len([line for line in lines if "queue full" in line]) == 3


def test_session_chunk_failures_are_logged_not_raised(tmp_path):
    class BrokenRecorder:
        def write(self, chunk):
            raise OSError("disk gone")

        def write_mix(self, chunk):
            return None

        def close(self):
            return {}

    adapter = FakeAdapter()
    lines = []
    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False),
        {CaptureSource.MIC: adapter},
        scheduler_factory=lambda source, on_final, on_partial, on_error: FakeScheduler(),
        recorder_factory=lambda *args, **kwargs: BrokenRecorder(),
        log=lines.append,
    )
    session.start()
    adapter.on_chunk(
        PcmChunk(CaptureSource.MIC, 48_000, 1, 0, np.ones((480, 1), dtype=np.float32), 1)
    )

    assert any("disk gone" in line for line in lines)
