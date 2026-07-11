"""MLX backend tests with mocked imports."""

import sys
import types

import numpy as np
import pytest

from src.core.asr.mlx_backend import MLXBackend


def test_load_transcribe_unload_is_idempotent_with_mocked_modules(monkeypatch):
    load_calls = {}
    transcribe_calls = {}
    decode_calls = {"count": 0}

    fake_gigaam = types.SimpleNamespace()

    def _load_model(model_type: str, repo_id: str):
        load_calls["model_type"] = model_type
        load_calls["repo_id"] = repo_id
        return "fake-model", "fake-tokenizer"

    fake_audio = types.SimpleNamespace(
        SAMPLE_RATE=16000,
        load_audio=lambda path: np.zeros(32000, dtype=np.float32),
        split_audio=lambda audio: [
            {"start_sample": 0, "end_sample": 16000, "start_sec": 0.0, "end_sec": 1.0},
            {"start_sample": 16000, "end_sample": 32000, "start_sec": 1.0, "end_sec": 2.0},
        ],
        compute_mel=lambda chunk: np.zeros((10, 64), dtype=np.float32),
    )

    def _decode(_encoded, _seq_len):
        values = ["", "hello", "world"]
        value = values[decode_calls["count"] % len(values)]
        decode_calls["count"] += 1
        return value

    fake_gigaam.load_model = _load_model
    fake_gigaam.audio = fake_audio
    fake_gigaam.load_audio = fake_audio.load_audio
    fake_gigaam.compute_mel = fake_audio.compute_mel
    fake_mlx = types.SimpleNamespace(
        array=lambda value: value,
        eval=lambda value: None,
        clear_cache=lambda: transcribe_calls.__setitem__("cache_cleared", True),
    )
    fake_tokenizer = types.SimpleNamespace(decode=lambda text: text.strip())
    backend = MLXBackend(repo="repo/test")
    monkeypatch.setitem(sys.modules, "gigaam_mlx", fake_gigaam)
    monkeypatch.setitem(sys.modules, "mlx", fake_mlx)
    monkeypatch.setitem(sys.modules, "mlx.core", fake_mlx)

    assert backend.load(lambda message: None) is True
    backend.model = types.SimpleNamespace(
        encode=lambda mel: (mel, mel.shape[1]),
        decode=_decode,
    )
    backend.tokenizer = fake_tokenizer
    assert load_calls == {
        "model_type": "rnnt",
        "repo_id": "repo/test",
    }

    segments = backend.transcribe_longform("/tmp/audio.wav")
    assert segments == [
        {"transcription": "hello", "boundaries": (1.0, 2.0)},
    ]
    assert backend.is_loaded()

    backend.unload()
    assert backend.model is None
    assert backend.tokenizer is None
    assert transcribe_calls.get("cache_cleared") is True


def test_transcribe_longform_reports_chunk_progress_from_chunking_loop(monkeypatch):
    fake_gigaam = types.SimpleNamespace()
    fake_audio = types.SimpleNamespace(
        SAMPLE_RATE=16000,
        load_audio=lambda path: np.zeros(48000, dtype=np.float32),
        split_audio=lambda audio: [
            {"start_sample": 0, "end_sample": 16000, "start_sec": 0.0, "end_sec": 1.0},
            {"start_sample": 16000, "end_sample": 32000, "start_sec": 1.0, "end_sec": 2.0},
            {"start_sample": 32000, "end_sample": 48000, "start_sec": 2.0, "end_sec": 3.0},
        ],
        compute_mel=lambda chunk: np.zeros((10, 64), dtype=np.float32),
    )
    fake_gigaam.audio = fake_audio
    fake_gigaam.load_audio = fake_audio.load_audio
    fake_gigaam.compute_mel = fake_audio.compute_mel
    fake_tokenizer = types.SimpleNamespace(decode=lambda text: text)

    backend = MLXBackend()
    backend.model = types.SimpleNamespace(
        encode=lambda mel: (mel, mel.shape[1]),
        decode=lambda encoded, seq_len: "text",
    )
    backend.tokenizer = fake_tokenizer
    backend._gigaam_mlx = fake_gigaam

    monkeypatch.setitem(sys.modules, "gigaam_mlx", fake_gigaam)
    monkeypatch.setitem(sys.modules, "mlx.core", types.SimpleNamespace(array=lambda value: value, eval=lambda value: None))
    events = []
    segments = backend._transcribe_in_chunks(
        "/tmp/audio.wav",
        progress_callback=lambda progress_value, processed, total: events.append(
            (progress_value, processed, total)
        ),
    )

    assert segments == [
        {"start": 0.0, "end": 1.0, "text": "text"},
        {"start": 1.0, "end": 2.0, "text": "text"},
        {"start": 2.0, "end": 3.0, "text": "text"},
    ]
    assert events == [
        (1 / 3, 1.0, 3.0),
        (2 / 3, 2.0, 3.0),
        (1.0, 3.0, 3.0),
    ]


def test_transcribe_in_chunks_callback_exception_is_propagated(monkeypatch):
    fake_gigaam = types.SimpleNamespace()
    fake_audio = types.SimpleNamespace(
        SAMPLE_RATE=16000,
        load_audio=lambda path: np.zeros(32000, dtype=np.float32),
        split_audio=lambda audio: [
            {"start_sample": 0, "end_sample": 16000, "start_sec": 0.0, "end_sec": 1.0},
        ],
        compute_mel=lambda chunk: np.zeros((10, 64), dtype=np.float32),
    )
    fake_gigaam.audio = fake_audio
    fake_gigaam.load_audio = fake_audio.load_audio
    fake_gigaam.compute_mel = fake_audio.compute_mel
    backend = MLXBackend()
    backend.model = types.SimpleNamespace(
        encode=lambda mel: (mel, mel.shape[1]),
        decode=lambda encoded, seq_len: "text",
    )
    backend.tokenizer = types.SimpleNamespace(decode=lambda text: text)
    backend._gigaam_mlx = fake_gigaam

    def raise_from_callback(*_args, **_kwargs):
        raise RuntimeError("callback-cancel")

    monkeypatch.setitem(sys.modules, "gigaam_mlx", fake_gigaam)
    monkeypatch.setitem(sys.modules, "mlx.core", types.SimpleNamespace(array=lambda value: value, eval=lambda value: None))

    with pytest.raises(RuntimeError, match="callback-cancel"):
        backend._transcribe_in_chunks(
            "/tmp/audio.wav",
            progress_callback=raise_from_callback,
        )


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
