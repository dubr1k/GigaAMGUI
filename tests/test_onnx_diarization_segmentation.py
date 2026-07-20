import sys
from types import SimpleNamespace

import numpy as np

from src.core.diarization import onnx_segmentation as segmentation_module
from src.core.diarization.onnx_segmentation import OnnxSegmentation


class _Session:
    def __init__(self, probabilities):
        self.probabilities = np.asarray(probabilities, dtype=np.float32)
        self.calls = []

    def run(self, outputs, feeds):
        self.calls.append(feeds["input_values"].shape)
        return [np.log(self.probabilities)[None, ...]]


def test_powerset_logits_preserve_single_and_overlapping_speakers():
    probabilities = np.asarray([
        [1.0, 1e-6, 1e-6, 1e-6, 1e-6, 1e-6, 1e-6],
        [1e-6, 0.8, 0.1, 0.1, 1e-6, 1e-6, 1e-6],
        [1e-6, 0.1, 0.1, 0.1, 0.6, 0.05, 0.05],
    ], dtype=np.float32)
    segmenter = OnnxSegmentation(session=_Session(probabilities), sample_rate=10)

    result = segmenter.infer(np.zeros(100, dtype=np.float32))

    assert result.probabilities.shape == (1, 3, 3)
    np.testing.assert_allclose(result.probabilities[0, 0], [3e-6, 3e-6, 3e-6], atol=1e-5)
    np.testing.assert_allclose(result.probabilities[0, 1], [0.800002, 0.100002, 0.100002], atol=1e-5)
    assert result.probabilities[0, 2, 0] > 0.6
    assert result.probabilities[0, 2, 1] > 0.6


def test_short_audio_is_padded_to_one_window():
    session = _Session(np.full((4, 7), 1 / 7, dtype=np.float32))
    segmenter = OnnxSegmentation(session=session, sample_rate=10)

    result = segmenter.infer(np.zeros(23, dtype=np.float32))

    assert session.calls == [(1, 1, 100)]
    assert result.window_starts.tolist() == [0.0]
    assert result.audio_duration == 2.3


def test_long_audio_keeps_overlapping_window_alignment():
    session = _Session(np.full((4, 7), 1 / 7, dtype=np.float32))
    segmenter = OnnxSegmentation(session=session, sample_rate=10)

    result = segmenter.infer(np.zeros(130, dtype=np.float32))

    assert result.window_starts.tolist() == [0.0, 5.0]
    assert len(session.calls) == 2


def test_segmentation_uses_matching_bundled_snapshot(monkeypatch, tmp_path):
    bundled = tmp_path / "segmentation"
    calls = []
    session = _Session(np.full((4, 7), 1 / 7, dtype=np.float32))
    vad = SimpleNamespace(_model=session)
    monkeypatch.setattr(
        segmentation_module,
        "resolve_model_dir",
        lambda repo_id, **_kwargs: bundled,
    )
    monkeypatch.setitem(
        sys.modules,
        "onnx_asr",
        SimpleNamespace(load_vad=lambda *args, **kwargs: calls.append((args, kwargs)) or vad),
    )

    OnnxSegmentation()._ensure_session()

    assert calls[0][1]["path"] == bundled
