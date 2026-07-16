"""Тесты ModelLoader без реальной загрузки весов."""

from types import SimpleNamespace

import numpy as np
import soundfile as sf
import torch

from src.core.asr.pytorch_backend import PyTorchBackend
from src.core.model_loader import ModelLoader


def test_default_revision_follows_selected_asr_model(monkeypatch):
    import src.core.model_loader as model_loader_module

    monkeypatch.setattr(model_loader_module, "ASR_MODEL", "multilingual_ctc")

    loader = model_loader_module.ModelLoader()

    assert loader.requested_model == "multilingual_ctc"


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
    loader._backend = PyTorchBackend(segmentation_mode="fixed_chunks")
    loader._backend.model = SimpleNamespace(
        _device="cpu",
        _dtype=torch.float32,
        head=object(),
        forward=lambda wav, length: (wav, length),
        decoding=SimpleNamespace(decode=lambda head, encoded, length: ["ok"]),
    )
    loader._backend.device = "cpu"

    result = loader.transcribe_longform(str(wav_path))

    assert result == [{"transcription": "ok", "boundaries": (0.0, 0.2)}]


def test_transcribe_longform_passes_progress_callback():
    received = []

    class DummyBackend:
        name = "dummy"

        def load(self, logger=None):
            return True

        def is_loaded(self):
            return True

        def transcribe_longform(self, audio_path, progress_callback=None):  # pragma: no cover
            if progress_callback:
                progress_callback(0.42, 10.0, 20.0)
            return [{"transcription": "text", "boundaries": (0.0, 1.0)}]

        def unload(self):
            return None

        def capabilities(self):
            from src.core.asr.types import BackendCapabilities

            return BackendCapabilities(backend="dummy", model="dummy", device="cpu")

    loader = ModelLoader()
    loader._backend = DummyBackend()

    output = loader.transcribe_longform("x.wav", progress_callback=lambda *args: received.append(args))

    assert output == [{"transcription": "text", "boundaries": (0.0, 1.0)}]
    assert received == [(0.42, 10.0, 20.0)]


def test_diagnostics_exposes_active_segmentation_mode():
    class DummyBackend:
        name = "dummy"

        def is_loaded(self):
            return True

        def capabilities(self):
            from src.core.asr.types import BackendCapabilities

            return BackendCapabilities(
                backend="dummy",
                model="dummy",
                device="cpu",
                segmentation_mode="fixed_chunks",
                segmentation_fallback_reason="VAD unavailable",
            )

    loader = ModelLoader()
    loader._backend = DummyBackend()

    diagnostics = loader.diagnostics()

    assert diagnostics["segmentation_mode"] == "fixed_chunks"
    assert diagnostics["segmentation_fallback_reason"] == "VAD unavailable"
