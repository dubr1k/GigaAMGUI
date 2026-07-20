import numpy as np
import pytest
import soundfile as sf

from src.core.diarization.base import SpeakerSegment
from src.core.diarization.onnx_backend import OnnxDiarizationBackend
from src.core.diarization.onnx_embeddings import EmbeddingResult
from src.core.diarization.onnx_segmentation import SegmentationResult
from src.core.model_preparation import PreparationState


class _Segmenter:
    def infer(self, waveform):
        return SegmentationResult(
            probabilities=np.asarray([[
                [0.9, 0.1, 0.0],
                [0.9, 0.1, 0.0],
                [0.1, 0.9, 0.0],
                [0.1, 0.9, 0.0],
            ]], dtype=np.float32),
            window_starts=np.asarray([0.0]),
            frame_step=0.5,
            audio_duration=2.0,
        )

    def unload(self):
        pass


class _Extractor:
    def __init__(self, valid=True):
        self.valid = valid

    def extract(self, waveform, segmentation):
        return EmbeddingResult(
            embeddings=np.asarray([[1.0, 0.0], [0.0, 1.0], [0.0, 0.0]], dtype=np.float32),
            valid=np.asarray([self.valid, self.valid, False]),
            window_indices=np.asarray([0, 0, 0]),
            local_speakers=np.asarray([0, 1, 2]),
        )

    def unload(self):
        pass


def _wav(tmp_path):
    path = tmp_path / "sample.wav"
    sf.write(path, np.zeros(32_000, dtype=np.float32), 16_000)
    return path


def test_onnx_manager_composes_complete_pipeline_and_renames_speakers(tmp_path):
    progress = []
    manager = OnnxDiarizationBackend(
        segmenter=_Segmenter(),
        embedding_extractor=_Extractor(),
    )

    segments = manager.diarize(
        str(_wav(tmp_path)),
        num_speakers=2,
        progress_callback=lambda *args: progress.append(args),
    )

    assert [(s.start, s.end, s.speaker) for s in segments] == [
        (0.0, 1.0, "Спикер №1"),
        (1.0, 2.0, "Спикер №2"),
    ]
    assert progress[-1][0] == 1.0


def test_onnx_manager_does_not_fabricate_single_speaker_on_embedding_failure(tmp_path):
    manager = OnnxDiarizationBackend(
        segmenter=_Segmenter(),
        embedding_extractor=_Extractor(valid=False),
    )

    with pytest.raises(ValueError, match="speaker embeddings"):
        manager.diarize(str(_wav(tmp_path)))


def test_onnx_manager_maps_words_with_shared_mapping_contract():
    manager = OnnxDiarizationBackend(segmenter=_Segmenter(), embedding_extractor=_Extractor())
    mapped = manager.map_speakers_to_transcription(
        [{
            "transcription": "раз два",
            "boundaries": (0.0, 2.0),
            "words": [
                {"text": "раз", "start": 0.1, "end": 0.8},
                {"text": "два", "start": 1.1, "end": 1.8},
            ],
        }],
        [
            SpeakerSegment(0, 1, "A"),
            SpeakerSegment(1, 2, "B"),
        ],
    )
    assert [item["speaker"] for item in mapped] == ["A", "B"]


def test_non_16k_audio_is_resampled_instead_of_rejected(tmp_path):
    """AUDIO_SAMPLE_RATE настраивается — падать на 48 кГц WAV нельзя."""
    path = tmp_path / "48k.wav"
    sf.write(path, np.zeros(48_000 * 2, dtype=np.float32), 48_000)
    seen = {}

    class _RecordingSegmenter(_Segmenter):
        def infer(self, waveform):
            seen["samples"] = len(waveform)
            return super().infer(waveform)

    manager = OnnxDiarizationBackend(
        segmenter=_RecordingSegmenter(),
        embedding_extractor=_Extractor(),
    )

    segments = manager.diarize(str(path), num_speakers=2)

    assert segments
    assert seen["samples"] == pytest.approx(32_000, rel=0.01)


def test_prepare_eagerly_downloads_and_initializes_both_onnx_models(monkeypatch):
    from src.core.diarization import onnx_backend

    calls = []
    events = []
    monkeypatch.setattr(onnx_backend, "hf_repo_is_cached", lambda _repo: False)

    class Segmenter:
        def _ensure_session(self):
            calls.append("segmentation")

        def unload(self):
            pass

    class Extractor:
        def _ensure_model(self):
            calls.append("embeddings")

        def unload(self):
            pass

    backend = OnnxDiarizationBackend(
        segmenter=Segmenter(),
        embedding_extractor=Extractor(),
    )

    prepared = backend.prepare(
        report=lambda state, **kwargs: events.append((state, kwargs)),
        cancel_check=lambda: False,
    )

    assert prepared is backend
    assert calls == ["segmentation", "embeddings"]
    assert [state for state, _kwargs in events] == [
        PreparationState.DOWNLOADING,
        PreparationState.LOADING,
        PreparationState.DOWNLOADING,
        PreparationState.LOADING,
    ]
