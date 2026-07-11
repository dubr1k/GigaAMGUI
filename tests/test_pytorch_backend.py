"""PyTorch backend unit tests with model/IO mocks."""

import sys
from types import SimpleNamespace

import numpy as np
import soundfile as sf
import torch

from src.core.asr.pytorch_backend import PyTorchBackend


def test_load_uses_project_runtime_manager(monkeypatch):
    calls = {}
    fake_gigaam = SimpleNamespace(
        load_model=lambda revision, **kwargs: calls.update(revision=revision, **kwargs) or object()
    )
    monkeypatch.setitem(sys.modules, "gigaam", fake_gigaam)
    monkeypatch.setattr(PyTorchBackend, "_select_device", lambda self: "cpu")

    backend = PyTorchBackend(model="ai-sage/GigaAM-v3", revision="e2e_rnnt")

    assert backend.load() is True
    assert calls["revision"] == "e2e_rnnt"


def test_bundled_download_root_prefers_local_bundle(tmp_path, monkeypatch):
    meipass = tmp_path / "meipass"
    model_dir = meipass / "models" / "gigaam"
    model_dir.mkdir(parents=True)
    (model_dir / "v3_e2e_rnnt.ckpt").write_bytes(b"0")
    (model_dir / "v3_e2e_rnnt_tokenizer.model").write_bytes(b"0")

    monkeypatch.setattr(__import__("sys"), "_MEIPASS", str(meipass), raising=False)
    backend = PyTorchBackend()
    assert backend._bundled_download_root() == str(model_dir)


def test_transcribe_longform_filters_empty_text_and_limits_chunks(tmp_path, monkeypatch):
    wav_path = tmp_path / "sample.wav"
    sf.write(wav_path, np.zeros(160000, dtype=np.float32), 16000)

    import torchaudio

    def fail_if_called(*args, **kwargs):
        raise AssertionError("torchaudio.load() must not be used")

    monkeypatch.setattr(torchaudio, "load", fail_if_called)

    backend = PyTorchBackend()
    called = {"chunks": 0}
    backend.model = SimpleNamespace(
        _device="cpu",
        _dtype=torch.float32,
        head=object(),
        forward=lambda wav, length: (wav, length),
        decoding=SimpleNamespace(decode=lambda head, encoded, length: ["", "hello"]),
    )
    backend.device = "cpu"

    result = backend.transcribe_longform(str(wav_path))
    # 160000 samples at 16000 Hz = 10s, so one chunk should be bounded by the real audio duration.
    assert result == [{"transcription": "hello", "boundaries": (0.0, 10.0)}]
    assert called["chunks"] == 0


def test_transcribe_longform_raises_on_unloaded_model():
    backend = PyTorchBackend()
    try:
        backend.transcribe_longform("/tmp/file.wav")
    except RuntimeError as exc:
        assert "Модель не загружена" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
