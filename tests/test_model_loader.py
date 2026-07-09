"""Тесты ModelLoader без реальной загрузки весов."""

from types import SimpleNamespace

import numpy as np
import soundfile as sf
import torch

from src.core.model_loader import ModelLoader


def test_empty_cache_safe_when_cpu():
    loader = ModelLoader()
    loader.device = "cpu"
    # Не должно бросать исключений на CPU
    loader._empty_cache()


def test_unload_clears_model():
    loader = ModelLoader()
    loader.model = object()  # заглушка вместо модели
    loader.device = "cpu"
    assert loader.is_loaded() is True
    loader.unload()
    assert loader.model is None
    assert loader.is_loaded() is False


def test_transcribe_longform_reads_wav_without_torchaudio_load(tmp_path, monkeypatch):
    """Регрессия #14: TorchAudio 2.9 не должен запускать TorchCodec.AudioDecoder."""
    wav_path = tmp_path / "sample.wav"
    sf.write(wav_path, np.full(3200, 0.25, dtype=np.float32), 16000)

    import torchaudio

    def fail_if_called(*args, **kwargs):
        raise AssertionError("torchaudio.load() must not be used")

    monkeypatch.setattr(torchaudio, "load", fail_if_called)

    loader = ModelLoader()
    loader.device = "cpu"
    loader.model = SimpleNamespace(
        _device="cpu",
        _dtype=torch.float32,
        head=object(),
        forward=lambda wav, length: (wav, length),
        decoding=SimpleNamespace(decode=lambda head, encoded, length: ["ok"]),
    )

    result = loader.transcribe_longform(str(wav_path))

    assert result == [{"transcription": "ok", "boundaries": (0.0, 0.2)}]
