import numpy as np

from src.core.diarization.onnx_embeddings import OnnxSpeakerEmbeddings
from src.core.diarization.onnx_segmentation import SegmentationResult


def _segmentation(probabilities, *, frame_step=0.5, duration=2.0):
    return SegmentationResult(
        probabilities=np.asarray(probabilities, dtype=np.float32),
        window_starts=np.asarray([0.0], dtype=np.float64),
        frame_step=frame_step,
        audio_duration=duration,
    )


def test_embeddings_are_batched_normalized_and_keep_local_ids():
    calls = []

    class Model:
        def embedding(self, waveforms):
            calls.append(waveforms)
            return np.asarray([[3.0, 4.0], [0.0, 2.0]], dtype=np.float32)

    extractor = OnnxSpeakerEmbeddings(
        model_factory=lambda **_: Model(),
        min_speech_seconds=0.5,
        sample_rate=4,
    )
    segmentation = _segmentation([[[0.9, 0.8, 0.1], [0.9, 0.8, 0.1], [0.1, 0.1, 0.1]]])

    result = extractor.extract(np.ones(8, dtype=np.float32), segmentation)

    assert len(calls) == 1 and len(calls[0]) == 2
    assert result.valid.tolist() == [True, True, False]
    assert result.window_indices.tolist() == [0, 0, 0]
    assert result.local_speakers.tolist() == [0, 1, 2]
    np.testing.assert_allclose(result.embeddings[:2], [[0.6, 0.8], [0.0, 1.0]])
    np.testing.assert_allclose(result.embeddings[2], [0.0, 0.0])


def test_short_speaker_regions_are_explicitly_invalid():
    extractor = OnnxSpeakerEmbeddings(
        model_factory=lambda **_: (_ for _ in ()).throw(AssertionError("must not load")),
        min_speech_seconds=1.0,
        sample_rate=4,
    )
    segmentation = _segmentation([[[0.9, 0.1, 0.1]]], frame_step=0.25, duration=0.25)

    result = extractor.extract(np.ones(1, dtype=np.float32), segmentation)

    assert result.valid.tolist() == [False, False, False]
    assert result.embeddings.shape == (3, 0)


def test_model_factory_receives_resolved_provider_chain():
    calls = []

    class Model:
        def embedding(self, waveforms):
            return np.asarray([[1.0]], dtype=np.float32)

    extractor = OnnxSpeakerEmbeddings(
        provider="cpu",
        model_factory=lambda **kwargs: calls.append(kwargs) or Model(),
        available_provider_probe=lambda: ("CPUExecutionProvider",),
        min_speech_seconds=0.1,
        sample_rate=4,
    )
    segmentation = _segmentation([[[0.9, 0.1, 0.1]]], frame_step=0.5, duration=0.5)

    extractor.extract(np.ones(2, dtype=np.float32), segmentation)

    assert calls == [{"providers": ["CPUExecutionProvider"], "model_dir": None}]
