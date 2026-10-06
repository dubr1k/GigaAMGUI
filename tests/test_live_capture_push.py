import numpy as np

from src.live.capture.base import CaptureAdapter
from src.live.capture.push import PushCaptureAdapter
from src.live.types import CaptureEventKind, CaptureSource


def _frames(n=1600):
    return np.zeros((n, 1), dtype=np.float32)


def _started(source=CaptureSource.MIC):
    adapter = PushCaptureAdapter(source)
    chunks, events = [], []
    adapter.start(chunks.append, events.append)
    return adapter, chunks, events


def test_push_adapter_satisfies_capture_protocol():
    assert isinstance(PushCaptureAdapter(CaptureSource.MIC), CaptureAdapter)


def test_push_delivers_pcm_chunk_with_source_offset_and_timestamp():
    adapter, chunks, _ = _started()

    adapter.push(seq=0, sample_offset=0, timestamp_ns=10, frames=_frames())
    adapter.push(seq=1, sample_offset=1600, timestamp_ns=110_000_000, frames=_frames())

    assert [c.sample_offset for c in chunks] == [0, 1600]
    assert chunks[0].source is CaptureSource.MIC
    assert chunks[0].sample_rate == 16_000 and chunks[0].channels == 1
    assert chunks[1].timestamp_ns == 110_000_000
    assert chunks[0].frames.shape == (1600, 1)


def test_push_reports_discontinuity_on_seq_gap_and_keeps_chunk():
    adapter, chunks, events = _started()

    adapter.push(seq=0, sample_offset=0, timestamp_ns=0, frames=_frames())
    adapter.push(seq=3, sample_offset=4800, timestamp_ns=300_000_000, frames=_frames())

    assert len(chunks) == 2
    assert [e.kind for e in events] == [CaptureEventKind.DISCONTINUITY]
    assert events[0].source is CaptureSource.MIC
    assert events[0].sample_offset == 4800
    assert "seq" in events[0].detail


def test_push_ignores_chunks_while_paused_and_after_stop():
    adapter, chunks, _ = _started()

    adapter.pause()
    adapter.push(seq=0, sample_offset=0, timestamp_ns=0, frames=_frames())
    adapter.resume()
    adapter.push(seq=1, sample_offset=1600, timestamp_ns=0, frames=_frames())
    adapter.stop()
    adapter.push(seq=2, sample_offset=3200, timestamp_ns=0, frames=_frames())

    assert [c.sample_offset for c in chunks] == [1600]


def test_push_before_start_is_ignored():
    adapter = PushCaptureAdapter(CaptureSource.SYSTEM)
    adapter.push(seq=0, sample_offset=0, timestamp_ns=0, frames=_frames())  # no callbacks, no error


def test_push_event_forwards_capture_event():
    adapter, _, events = _started(CaptureSource.SYSTEM)

    adapter.event(CaptureEventKind.PERMISSION_DENIED, "screen recording denied")

    assert events[0].kind is CaptureEventKind.PERMISSION_DENIED
    assert events[0].source is CaptureSource.SYSTEM
    assert events[0].detail == "screen recording denied"


def test_push_devices_describes_one_virtual_device():
    devices = PushCaptureAdapter(CaptureSource.MIC).devices()
    assert len(devices) == 1
    assert devices[0].source is CaptureSource.MIC
    assert devices[0].sample_rate == 16_000
    assert devices[0].is_default
