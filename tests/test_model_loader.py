"""Тесты ModelLoader без реальной загрузки весов."""

import weakref
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


def test_unload_releases_last_model_reference_before_backend_cache_cleanup():
    class Weights:
        pass

    released_at_cleanup = []

    class Backend:
        def __init__(self):
            self.model = Weights()

        def unload(self):
            self.model = None
            released_at_cleanup.append(weights() is None)

    loader = ModelLoader()
    loader._backend = Backend()
    loader.model = loader._backend.model
    weights = weakref.ref(loader.model)
    loader.unload()
    assert released_at_cleanup == [True]
    assert weights() is None


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


def test_diagnostics_exposes_onnx_provider_independently_from_backend():
    class DummyBackend:
        name = "onnx"

        def is_loaded(self):
            return True

        def capabilities(self):
            from src.core.asr.types import BackendCapabilities

            return BackendCapabilities(
                backend="onnx",
                model="v3_e2e_rnnt",
                device="cuda",
                provider="CUDAExecutionProvider",
                quantization="int8",
                provider_fallback_reason="fallback",
            )

    loader = ModelLoader(requested_backend="onnx")
    loader._backend = DummyBackend()

    diagnostics = loader.diagnostics()

    assert diagnostics["requested_provider"] == "auto"
    assert diagnostics["provider"] == "CUDAExecutionProvider"
    assert diagnostics["quantization"] == "int8"
    assert diagnostics["provider_fallback_reason"] == "fallback"


def test_configure_onnx_provider_unloads_backend():
    loader = ModelLoader(requested_backend="onnx", onnx_provider="cpu")
    loader._backend = object()

    loader.configure_onnx_runtime(provider="cuda")

    assert loader.requested_provider == "cuda"
    assert loader._backend is None


def test_configure_model_also_selects_backend_model_name(monkeypatch):
    # Фабрика строит MLXBackend(model=model_name). configure_model менял только
    # revision, и после переключения с multilingual_ctc на v3_e2e_rnnt MLX
    # получал старое имя, падал на загрузке и молча уходил в PyTorch.
    import src.core.model_loader as model_loader_module

    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(name="mlx", is_loaded=lambda: False), None

    monkeypatch.setattr(model_loader_module, "create_backend_from_config", factory)
    loader = ModelLoader(model_name="multilingual_ctc", model_revision="multilingual_ctc")

    loader.configure_model("v3_e2e_rnnt")
    loader._ensure_backend()

    assert captured["model_revision"] == "v3_e2e_rnnt"
    assert captured["model_name"] == "v3_e2e_rnnt"


def test_missing_pytorch_resources_name_the_absent_tokenizer(tmp_path, monkeypatch):
    model_dir = tmp_path / "gigaam"
    model_dir.mkdir()
    (model_dir / "v3_e2e_rnnt.ckpt").write_bytes(b"ckpt")
    monkeypatch.setenv("GIGAAM_PYTORCH_MODEL_DIR", str(model_dir))
    monkeypatch.setattr(PyTorchBackend, "_bundled_download_root", lambda self: None)

    loader = ModelLoader(requested_backend="pytorch", model_revision="v3_e2e_rnnt")

    assert loader.missing_asr_resources() == ("GigaAM tokenizer: v3_e2e_rnnt_tokenizer.model",)
