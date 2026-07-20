from src.core.diarization.base import DiarizationBackend, SpeakerSegment
from src.core.diarization.factory import create_diarization_backend


class _Backend:
    backend = "onnx"

    def diarize(self, audio_path, num_speakers=None, progress_callback=None):
        return [SpeakerSegment(0.0, 1.0, "SPEAKER_00")]

    def map_speakers_to_transcription(self, transcription_segments, speaker_segments):
        return transcription_segments

    def unload(self):
        return None


def test_diarization_protocol_is_structural():
    assert isinstance(_Backend(), DiarizationBackend)


def test_legacy_manager_satisfies_protocol_without_loading_model():
    from src.utils.diarization import DiarizationManager

    assert isinstance(DiarizationManager(hf_token="hf_test", device="cpu"), DiarizationBackend)


def test_factory_routes_onnx_without_loading_legacy_stack():
    calls = []
    backend = create_diarization_backend(
        "onnx",
        provider="cuda",
        onnx_factory=lambda **kwargs: calls.append(kwargs) or _Backend(),
    )

    assert backend.backend == "onnx"
    assert calls == [{"provider": "cuda"}]


def test_factory_routes_legacy_backends():
    calls = []
    backend = create_diarization_backend(
        "sortformer",
        device="cpu",
        nemo_available=True,
        legacy_factory=lambda **kwargs: calls.append(kwargs) or _Backend(),
    )

    assert backend.backend == "onnx"
    assert calls == [{"backend": "sortformer", "hf_token": None, "device": "cpu"}]
