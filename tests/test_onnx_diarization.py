import numpy as np
import pytest
import soundfile as sf

from src.core.diarization.base import SpeakerSegment
from src.core.diarization.onnx_backend import OnnxDiarizationBackend
from src.core.diarization.onnx_embeddings import EmbeddingResult
from src.core.diarization.onnx_segmentation import SegmentationResult


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
