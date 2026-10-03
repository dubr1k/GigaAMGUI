import numpy as np
import pytest

from src.live.capture.base import CaptureAdapter
from src.live.types import CaptureDevice, CaptureEvent, CaptureEventKind, CaptureSource, PcmChunk


def test_pcm_chunk_rejects_wrong_frame_shape():
    with pytest.raises(ValueError, match="frames"):
        PcmChunk(CaptureSource.MIC, 48_000, 2, 0, np.zeros((10,), dtype=np.float32), 1)


def test_pcm_chunk_rejects_backend_owned_or_non_float32_frames():
    backend_frames = np.zeros((2, 1), dtype=np.float32)
    view = backend_frames[:]

    with pytest.raises(ValueError, match="own"):
        PcmChunk(CaptureSource.MIC, 48_000, 1, 0, view, 1)

    with pytest.raises(ValueError, match="float32"):
        PcmChunk(CaptureSource.MIC, 48_000, 1, 0, np.zeros((2, 1)), 1)


def test_pcm_chunk_owns_immutable_frame_data():
    frames = np.zeros((2, 1), dtype=np.float32)
    chunk = PcmChunk(CaptureSource.MIC, 48_000, 1, 0, frames, 1)
    frames[0, 0] = 1.0

    assert chunk.frames.flags["OWNDATA"]
    assert chunk.frames.flags.writeable is False
    assert chunk.frames[0, 0] == 0.0
    with pytest.raises(ValueError):
        chunk.frames[0, 0] = 1.0


def test_capture_adapter_protocol_accepts_fake_adapter():
    adapter: CaptureAdapter = _FakeAdapter()

    assert isinstance(adapter, CaptureAdapter)
    assert adapter.devices()[0].source is CaptureSource.MIC


class _FakeAdapter:
    def start(self, on_chunk, on_event):
        self.on_chunk = on_chunk
        self.on_event = on_event

    def pause(self):
        return None

    def resume(self):
        return None

    def stop(self):
        return None

    def devices(self):
        return [CaptureDevice("mic-default", "Microphone", CaptureSource.MIC, 48_000, 1, True)]


def test_capture_event_preserves_source_offset_and_metadata():
    event = CaptureEvent(CaptureEventKind.OVERFLOW, CaptureSource.SYSTEM, 48_000, 12, "queue full")

    assert event.sample_offset == 48_000
    assert event.detail == "queue full"


def test_settings_for_sources_record_tracks_only_for_captured_sources():
    from src.live.types import LiveSettings

    both = LiveSettings.for_sources([CaptureSource.MIC, CaptureSource.SYSTEM], record_system=False)
    mic_only = LiveSettings.for_sources([CaptureSource.MIC], diarization_backend="pyannote")

    assert (both.record_mic_audio, both.record_system_audio, both.record_mix_audio) == (True, False, True)
    assert (mic_only.record_mic_audio, mic_only.record_system_audio, mic_only.record_mix_audio) == (True, False, False)
    assert mic_only.diarization_backend == "pyannote"
    assert LiveSettings.for_sources([CaptureSource.MIC], record_mic=False, record_system=False).record_source_audio is False


def test_asr_scheduler_factory_shares_one_backend_and_honours_the_given_class():
    from src.live.asr import asr_scheduler_factory

    made = []

    class Scheduler:
        def __init__(self, backend, **callbacks):
            made.append((backend, sorted(callbacks)))

    backend = object()
    factory = asr_scheduler_factory(backend, scheduler_class=Scheduler)
    factory(CaptureSource.MIC, print, print, print)
    factory(CaptureSource.SYSTEM, print, print, print)

    assert made == [(backend, ["on_error", "on_final", "on_partial"])] * 2
