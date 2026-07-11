"""MLX backend tests with mocked imports."""

import sys
import types

import pytest

from src.core.asr.mlx_backend import MLXBackend


def test_load_transcribe_unload_is_idempotent_with_mocked_modules(monkeypatch):
    load_calls = {}
    transcribe_calls = {}

    fake_gigaam = types.SimpleNamespace()

    def _load_model(model_type: str, repo_id: str):
        load_calls["model_type"] = model_type
        load_calls["repo_id"] = repo_id
        return "fake-model", "fake-tokenizer"

    def _transcribe_file(audio_path, model, tokenizer, model_type, verbose=False):
        transcribe_calls["audio_path"] = audio_path
        transcribe_calls["model_type"] = model_type
        return [
            {"start": 0.0, "end": 1.0, "text": ""},
            {"start": 1.0, "end": 1.2, "text": " hello "},
            {"start": 2.0, "end": 2.5, "text": "world"},
        ]

    fake_gigaam.load_model = _load_model
    fake_gigaam.transcribe_file = _transcribe_file
    fake_mlx = types.SimpleNamespace(clear_cache=lambda: transcribe_calls.__setitem__("cache_cleared", True))

    monkeypatch.setitem(sys.modules, "gigaam_mlx", fake_gigaam)
    monkeypatch.setitem(sys.modules, "mlx", fake_mlx)

    backend = MLXBackend(repo="repo/test")
    assert backend.load(lambda message: None) is True
    assert load_calls == {
        "model_type": "rnnt",
        "repo_id": "repo/test",
    }

    segments = backend.transcribe_longform("/tmp/audio.wav")
    assert segments == [
        {"transcription": "hello", "boundaries": (1.0, 1.2)},
        {"transcription": "world", "boundaries": (2.0, 2.5)},
    ]
    assert backend.is_loaded()

    backend.unload()
    assert backend.model is None
    assert backend.tokenizer is None
    assert transcribe_calls.get("cache_cleared") is True


def test_gigaam_revision_name_is_normalized_for_upstream_api(monkeypatch):
    load_calls = {}
    fake_gigaam = types.SimpleNamespace(
        load_model=lambda **kwargs: load_calls.update(kwargs) or ("model", "tokenizer")
    )
    monkeypatch.setitem(sys.modules, "gigaam_mlx", fake_gigaam)

    backend = MLXBackend(model="e2e_rnnt", repo="repo/test")

    assert backend.load() is True
    assert load_calls["model_type"] == "rnnt"


def test_transcribe_without_load_raises(monkeypatch):
    backend = MLXBackend()
    with pytest.raises(RuntimeError):
        backend.transcribe_longform("/tmp/audio.wav")
