import importlib
import sys
import types

from src.utils import pyannote_patch, torch_patch


def _new_torchaudio_module():
    module = types.ModuleType("torchaudio")
    module.__path__ = []
    module.__version__ = "2.11.0"

    class AudioMetaData:
        pass

    module.AudioMetaData = AudioMetaData
    return module


def test_torchaudio_patch_registers_backend_common_as_importable_package(monkeypatch):
    torchaudio = _new_torchaudio_module()
    monkeypatch.setitem(sys.modules, "torchaudio", torchaudio)
    monkeypatch.delitem(sys.modules, "torchaudio.backend", raising=False)
    monkeypatch.delitem(sys.modules, "torchaudio.backend.common", raising=False)

    assert pyannote_patch.apply_torchaudio_backend_patch() is True

    backend = importlib.import_module("torchaudio.backend")
    common = importlib.import_module("torchaudio.backend.common")
    assert hasattr(backend, "__path__")
    assert common.AudioMetaData is torchaudio.AudioMetaData
    assert torchaudio.list_audio_backends() == ["soundfile"]


def test_torchaudio_patch_preserves_existing_backend_modules(monkeypatch):
    torchaudio = _new_torchaudio_module()
    backend = types.ModuleType("torchaudio.backend")
    backend.__path__ = []
    common = types.ModuleType("torchaudio.backend.common")
    common.AudioMetaData = torchaudio.AudioMetaData
    backend.common = common
    torchaudio.backend = backend
    monkeypatch.setitem(sys.modules, "torchaudio", torchaudio)
    monkeypatch.setitem(sys.modules, "torchaudio.backend", backend)
    monkeypatch.setitem(sys.modules, "torchaudio.backend.common", common)

    assert pyannote_patch.apply_torchaudio_backend_patch() is True
    assert sys.modules["torchaudio.backend"] is backend
    assert sys.modules["torchaudio.backend.common"] is common


def test_torchaudio_patch_repairs_old_non_package_stub(monkeypatch):
    torchaudio = _new_torchaudio_module()
    torchaudio.set_audio_backend = lambda _backend: None
    torchaudio.get_audio_backend = lambda: "soundfile"
    backend = types.ModuleType("torchaudio.backend")
    torchaudio.backend = backend
    monkeypatch.setitem(sys.modules, "torchaudio", torchaudio)
    monkeypatch.setitem(sys.modules, "torchaudio.backend", backend)
    monkeypatch.delitem(sys.modules, "torchaudio.backend.common", raising=False)

    assert pyannote_patch.apply_torchaudio_backend_patch() is True
    common = importlib.import_module("torchaudio.backend.common")
    assert hasattr(backend, "__path__")
    assert common.AudioMetaData is torchaudio.AudioMetaData


def test_torch_load_patch_is_reapplied_after_runtime_switch(monkeypatch):
    monkeypatch.delenv("GIGAAM_DISABLE_TORCH_PATCH", raising=False)
    monkeypatch.setattr(torch_patch, "_TORCH_PATCH_APPLIED", False)

    def make_torch(label):
        module = types.ModuleType("torch")
        module.__version__ = "2.8.0"
        module.load = lambda *args, **kwargs: (label, args, kwargs)
        return module

    first = make_torch("first")
    monkeypatch.setitem(sys.modules, "torch", first)
    assert torch_patch.apply_torch_load_patch() is True
    assert first.load("model.pt")[2]["weights_only"] is False

    second = make_torch("second")
    monkeypatch.setitem(sys.modules, "torch", second)
    assert torch_patch.apply_torch_load_patch() is True
    assert second.load("model.pt")[2]["weights_only"] is False
