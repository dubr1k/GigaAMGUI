import time
from threading import Event

import numpy as np
import pytest

from src.live.types import CaptureSource


class FakeWindowsApi:
    def __init__(self):
        self.callback = None
        self.paused = False
        self.stopped = False

    def devices(self, source):
        return [
            {
                "id": "mic-1" if source is CaptureSource.MIC else "loopback-1",
                "name": "Default",
                "sample_rate": 48_000,
                "channels": 2,
                "is_default": True,
            }
        ]

    def start(self, source, device_id, callback):
        self.callback = callback

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def stop(self):
        self.stopped = True


def wait_until(predicate):
    end = time.monotonic() + 1
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def test_windows_callback_copies_then_delivers_chunk_from_worker():
    from src.live.capture.windows import WindowsMicrophoneAdapter

    api = FakeWindowsApi()
    chunks = []
    adapter = WindowsMicrophoneAdapter(api=api)
    adapter.start(chunks.append, lambda event: None)
    frames = np.ones((4, 2), dtype=np.float32)

    api.callback(frames, 123)
    frames.fill(0)
    wait_until(lambda: chunks)
    adapter.stop()

    assert chunks[0].source is CaptureSource.MIC
    assert chunks[0].sample_offset == 0
    assert chunks[0].timestamp_ns == 123
    assert chunks[0].frames.tolist() == [[1.0, 1.0]] * 4
    assert not chunks[0].frames.flags.writeable


def test_windows_callback_returns_while_worker_consumer_is_blocked():
    from src.live.capture.windows import WindowsMicrophoneAdapter

    api = FakeWindowsApi()
    consumer_entered = Event()
    release_consumer = Event()
    adapter = WindowsMicrophoneAdapter(api=api)

    def consume(chunk):
        consumer_entered.set()
        release_consumer.wait(timeout=1)

    adapter.start(consume, lambda event: None)
    started = time.monotonic()
    api.callback(np.ones((4, 1), dtype=np.float32), 123)
    elapsed = time.monotonic() - started
    assert elapsed < 0.1
    wait_until(consumer_entered.is_set)
    release_consumer.set()
    adapter.stop()


def test_windows_preserves_native_callback_sample_rate():
    from src.live.capture.windows import WindowsMicrophoneAdapter

    api = FakeWindowsApi()
    chunks = []
    adapter = WindowsMicrophoneAdapter(api=api)
    adapter.start(chunks.append, lambda event: None)
    api.callback(np.ones((4, 1), dtype=np.float32), 123, 44_100)
    wait_until(lambda: chunks)
    adapter.stop()

    assert chunks[0].sample_rate == 44_100


def test_windows_native_api_restarts_paused_stream():
    from src.live.capture.windows import _PyAudioWASAPI

    class Stream:
        starts = 0

        def start_stream(self):
            self.starts += 1

    native = object.__new__(_PyAudioWASAPI)
    stream = Stream()
    native._stream = stream

    native.resume()

    assert stream.starts == 1


def test_windows_stop_drains_already_queued_frames():
    from src.live.capture.windows import WindowsMicrophoneAdapter

    api = FakeWindowsApi()
    chunks = []
    adapter = WindowsMicrophoneAdapter(api=api)
    adapter.start(chunks.append, lambda event: None)
    api.callback(np.ones((4, 1), dtype=np.float32), 123)
    adapter.stop()

    assert len(chunks) == 1


def test_windows_device_enumeration_preserves_loopback_capability():
    from src.live.capture.windows import WindowsSystemAudioAdapter

    devices = WindowsSystemAudioAdapter(api=FakeWindowsApi()).devices()

    assert [(device.id, device.source, device.channels) for device in devices] == [
        ("loopback-1", CaptureSource.SYSTEM, 2)
    ]


def test_windows_missing_runtime_is_actionable():
    from src.live.capture.factory import CaptureUnavailable
    from src.live.capture.windows import WindowsMicrophoneAdapter

    with pytest.raises(CaptureUnavailable, match="PyAudioWPatch"):
        WindowsMicrophoneAdapter(api_loader=lambda: (_ for _ in ()).throw(ImportError("missing"))).devices()
