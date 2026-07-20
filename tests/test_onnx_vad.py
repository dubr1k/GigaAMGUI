import numpy as np
import pytest
import soundfile as sf

from src.core.asr import onnx_vad as onnx_vad_module
from src.core.asr.onnx_vad import OnnxVadSegmenter
from src.core.asr.vad import VadUnavailableError


class _FakeVad:
    def __init__(self, segments):
        self.segments = segments
        self.calls = []

    def segment_batch(self, waveforms, waveform_lens, sample_rate):
        self.calls.append((waveforms.copy(), waveform_lens.copy(), sample_rate))
        return iter((iter(self.segments),))


def test_segment_file_converts_samples_to_seconds(tmp_path):
    wav_path = tmp_path / "vad.wav"
    sf.write(wav_path, np.zeros(16000, dtype=np.float32), 16000)
    fake_vad = _FakeVad([(1600, 8000)])
    factory_calls = []
    segmenter = OnnxVadSegmenter(
        provider="cpu",
        vad_factory=lambda *args, **kwargs: factory_calls.append((args, kwargs)) or fake_vad,
        available_provider_probe=lambda: ("CPUExecutionProvider",),
    )

    result = segmenter.segment_file(str(wav_path), audio_duration=1.0)

    assert result == [(0.1, 0.5)]
    assert factory_calls == [
        (
            ("silero",),
            {
                "path": None,
                "quantization": None,
                "providers": ["CPUExecutionProvider"],
            },
        )
    ]
    assert fake_vad.calls[0][0].shape == (1, 16000)
    assert fake_vad.calls[0][2] == 16000


def test_missing_segment_batch_is_reported_before_audio_read():
    segmenter = OnnxVadSegmenter(
        vad_factory=lambda *args, **kwargs: object(),
        available_provider_probe=lambda: ("CPUExecutionProvider",),
    )

    with pytest.raises(VadUnavailableError, match="segment_batch"):
        segmenter.segment_file("missing.wav", audio_duration=1.0)


def test_vad_uses_its_own_bundled_snapshot(monkeypatch, tmp_path):
    calls = []
    bundled = tmp_path / "silero"
    monkeypatch.setattr(
        onnx_vad_module,
        "resolve_model_dir",
        lambda repo_id, **_kwargs: bundled if repo_id == "istupakov/silero-vad-onnx" else None,
    )
    segmenter = OnnxVadSegmenter(
        vad_factory=lambda *args, **kwargs: calls.append((args, kwargs)) or _FakeVad([]),
        available_provider_probe=lambda: ("CPUExecutionProvider",),
    )

    segmenter._ensure_vad()

    assert calls[0][1]["path"] == bundled


def test_unsupported_sample_rate_is_reported(tmp_path):
    wav_path = tmp_path / "vad.wav"
    sf.write(wav_path, np.zeros(22050, dtype=np.float32), 22050)
    segmenter = OnnxVadSegmenter(
        vad_factory=lambda *args, **kwargs: _FakeVad([]),
        available_provider_probe=lambda: ("CPUExecutionProvider",),
    )

    with pytest.raises(VadUnavailableError, match="22050"):
        segmenter.segment_file(str(wav_path), audio_duration=1.0)
