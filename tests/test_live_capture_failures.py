"""Native capture failures: a source that never started is failed."""

import time

import pytest

from src.live.capture.common import QueuedCaptureAdapter
from src.live.capture.factory import CaptureUnavailable
from src.live.session import LiveSession
from src.live.types import CaptureEventKind, CaptureSource, CaptureState, LiveSettings


class PortAudioError(Exception):
    """sounddevice.PortAudioError derives from Exception, not OSError."""


class SilentApi:
    def __init__(self, start_error=None):
        self.start_error = start_error
        self.callback = None
        self.stopped = False

    def devices(self, source):
        return []

    def start(self, source, device_id, callback):
        if self.start_error is not None:
            raise self.start_error
        self.callback = callback

    def pause(self):
        return None

    def resume(self):
        return None

    def stop(self):
        self.stopped = True


class FakeAdapter:
    def start(self, on_chunk, on_event):
        self.on_chunk = on_chunk

    def pause(self):
        return None

    def resume(self):
        return None

    def stop(self):
        return None


class NullScheduler:
    def submit(self, chunk):
        return None

    def flush(self):
        return None

    def close(self, timeout=None):
        return True


def wait_until(predicate, timeout=1.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


@pytest.mark.parametrize(
    "error",
    [
        CaptureUnavailable("No PipeWire/PulseAudio monitor source is available."),
        PortAudioError("Error opening InputStream: Invalid number of channels"),
        RuntimeError("native capture API is not configured"),
        OSError("No WASAPI loopback device is available."),
    ],
)
def test_a_source_whose_start_failed_is_reported_as_failed(error):
    """Non-OSError start failures came out as STATUS, so the source stayed
    active and the session claimed RECORDING with nothing being captured."""
    events = []
    adapter = QueuedCaptureAdapter(CaptureSource.SYSTEM, SilentApi(start_error=error))
    adapter.start(lambda chunk: None, events.append)
    wait_until(lambda: events)
    adapter.stop()

    assert events[-1].kind is CaptureEventKind.DEVICE_REMOVED
    assert str(error) in events[-1].detail


def test_permission_words_in_a_start_failure_still_mean_permission_denied():
    events = []
    adapter = QueuedCaptureAdapter(
        CaptureSource.SYSTEM, SilentApi(start_error=RuntimeError("Capture not authorized by TCC")),
    )
    adapter.start(lambda chunk: None, events.append)
    wait_until(lambda: events)
    adapter.stop()

    assert events[-1].kind is CaptureEventKind.PERMISSION_DENIED


def test_session_fails_a_source_that_could_not_open_its_device(tmp_path):
    system = QueuedCaptureAdapter(CaptureSource.SYSTEM, SilentApi(start_error=PortAudioError("device busy")))
    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False),
        {CaptureSource.MIC: FakeAdapter(), CaptureSource.SYSTEM: system},
        scheduler_factory=lambda source, on_final, on_partial, on_error: NullScheduler(),
    )
    session.start()
    try:
        wait_until(lambda: CaptureSource.SYSTEM in session.status().failed_sources)
        assert session.status().active_sources == {CaptureSource.MIC}
        assert session.status().state is CaptureState.RECORDING
    finally:
        session.stop()
