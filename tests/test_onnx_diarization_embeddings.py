import numpy as np
import pytest

from src.core.diarization import onnx_embeddings as embeddings_module
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


def test_embeddings_are_extracted_in_bounded_batches():
    """Один общий вызов запрашивал ~600 МБ и падал на GPU с 4 ГБ."""
    batches = []

    class Model:
        def embedding(self, waveforms):
            batches.append(len(waveforms))
            return np.tile(np.asarray([[1.0, 0.0]], dtype=np.float32), (len(waveforms), 1))

    extractor = OnnxSpeakerEmbeddings(
        model_factory=lambda **_: Model(),
        min_speech_seconds=0.25,
        sample_rate=4,
        batch_size=2,
    )
    probabilities = [[[0.9, 0.9, 0.9]] * 2] * 3
    segmentation = SegmentationResult(
        probabilities=np.asarray(probabilities, dtype=np.float32),
        window_starts=np.asarray([0.0, 0.5, 1.0], dtype=np.float64),
        frame_step=0.25,
        audio_duration=2.0,
    )

    result = extractor.extract(np.ones(16, dtype=np.float32), segmentation)

    assert int(result.valid.sum()) == 9
    assert batches == [2, 2, 2, 2, 1]
    assert result.embeddings.shape == (9, 2)


def test_batch_is_halved_when_accelerator_runs_out_of_memory():
    """Свободную VRAM заранее не узнать, поэтому OOM ловится и батч уменьшается."""
    attempts = []

    class Model:
        def embedding(self, waveforms):
            attempts.append(len(waveforms))
            if len(waveforms) > 1:
                raise RuntimeError(
                    "[ONNXRuntimeError] : 6 : RUNTIME_EXCEPTION : "
                    "Failed to allocate memory for requested buffer of size 601667840"
                )
            return np.asarray([[1.0, 0.0]], dtype=np.float32)

    extractor = OnnxSpeakerEmbeddings(
        model_factory=lambda **_: Model(),
        min_speech_seconds=0.25,
        sample_rate=4,
        batch_size=4,
    )
    segmentation = _segmentation(
        [[[0.9, 0.1, 0.1], [0.9, 0.1, 0.1]]], frame_step=0.25, duration=0.5
    )

    result = extractor.extract(np.ones(2, dtype=np.float32), segmentation)

    assert attempts[0] == 1  # один валидный отрезок, батч не превышает его
    assert int(result.valid.sum()) == 1


def test_non_memory_errors_are_not_retried():
    calls = []

    class Model:
        def embedding(self, waveforms):
            calls.append(len(waveforms))
            raise RuntimeError("модель повреждена")

    extractor = OnnxSpeakerEmbeddings(
        model_factory=lambda **_: Model(),
        min_speech_seconds=0.25,
        sample_rate=4,
        batch_size=4,
    )
    segmentation = _segmentation(
        [[[0.9, 0.1, 0.1], [0.9, 0.1, 0.1]]], frame_step=0.25, duration=0.5
    )

    with pytest.raises(RuntimeError, match="повреждена"):
        extractor.extract(np.ones(2, dtype=np.float32), segmentation)
    assert len(calls) == 1


def test_only_active_speech_reaches_the_embedding_model():
    """Тишина не должна разбавлять эмбеддинг — она вырезается, а не зануляется."""
    captured = []

    class Model:
        def embedding(self, waveforms):
            captured.extend(waveforms)
            return np.asarray([[1.0, 0.0]], dtype=np.float32)

    extractor = OnnxSpeakerEmbeddings(
        model_factory=lambda **_: Model(),
        min_speech_seconds=0.5,
        sample_rate=4,
    )
    # Речь только в первом фрейме окна; остальные два — тишина.
    segmentation = _segmentation([[[0.9, 0.1, 0.1], [0.1, 0.1, 0.1], [0.1, 0.1, 0.1]]])
    audio = np.concatenate(
        [
            np.ones(2, dtype=np.float32),
            np.full(4, 0.25, dtype=np.float32),
        ]
    )

    result = extractor.extract(audio, segmentation)

    assert result.valid.tolist() == [True, False, False]
    assert len(captured) == 1
    np.testing.assert_allclose(captured[0], np.ones(2, dtype=np.float32))


def test_embeddings_use_matching_bundled_snapshot(monkeypatch, tmp_path):
    calls = []
    bundled = tmp_path / "wespeaker"
    monkeypatch.setattr(
        embeddings_module,
        "resolve_model_dir",
        lambda repo_id, **_kwargs: bundled,
    )
    extractor = OnnxSpeakerEmbeddings(
        model_factory=lambda **kwargs: calls.append(kwargs) or object(),
        available_provider_probe=lambda: ("CPUExecutionProvider",),
    )

    extractor._ensure_model()

    assert calls[0]["model_dir"] == bundled
