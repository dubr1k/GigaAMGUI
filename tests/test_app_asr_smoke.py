"""Frozen-app ASR runtime smoke tests."""

import sys
import types

import app


def test_run_asr_runtime_smoke_evaluates_mlx_array(monkeypatch):
    calls = {}
    fake_core = types.SimpleNamespace(
        array=lambda values: tuple(values),
        eval=lambda value: calls.setdefault("evaluated", value),
    )
    fake_mlx = types.ModuleType("mlx")
    fake_mlx.core = fake_core
    fake_gigaam_mlx = types.ModuleType("gigaam_mlx")
    fake_gigaam_mlx.__version__ = "0.1.0"
    monkeypatch.setitem(sys.modules, "mlx", fake_mlx)
    monkeypatch.setitem(sys.modules, "mlx.core", fake_core)
    monkeypatch.setitem(sys.modules, "gigaam_mlx", fake_gigaam_mlx)

    result = app.run_asr_runtime_smoke()

    assert calls["evaluated"] == (1.0, 2.0)
    assert result == {"backend": "mlx", "gigaam_mlx": "0.1.0"}


def test_run_asr_model_smoke_loads_rnnt_and_transcribes(monkeypatch, tmp_path):
    calls = {}
    fake_gigaam_mlx = types.ModuleType("gigaam_mlx")

    def load_model(model_type):
        calls["model_type"] = model_type
        return "model", "tokenizer"

    def transcribe_file(path, **kwargs):
        calls["audio_path"] = path
        return []

    fake_gigaam_mlx.load_model = load_model
    fake_gigaam_mlx.transcribe_file = transcribe_file
    monkeypatch.setitem(sys.modules, "gigaam_mlx", fake_gigaam_mlx)
    audio_path = tmp_path / "silence.wav"
    audio_path.write_bytes(b"wav")

    result = app.run_asr_model_smoke(str(audio_path))

    assert calls["model_type"] == "rnnt"
    assert calls["audio_path"] == str(audio_path)
    assert result == {"backend": "mlx", "model": "rnnt", "segments": 0}


def test_run_media_download_smoke_uses_project_downloader(monkeypatch, tmp_path):
    calls = {}

    class FakeDownloader:
        def download(self, url, target_dir):
            calls.update(url=url, target_dir=target_dir)
            return types.SimpleNamespace(files=[str(tmp_path / "audio.webm")])

    monkeypatch.setattr("src.utils.media_downloader.MediaDownloader", FakeDownloader)

    result = app.run_media_download_smoke("https://example.test/video", str(tmp_path))

    assert calls == {
        "url": "https://example.test/video",
        "target_dir": str(tmp_path),
    }
    assert result == {"files": [str(tmp_path / "audio.webm")]}
