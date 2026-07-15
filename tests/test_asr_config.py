"""Тесты конфигурации ASR backend."""

import importlib
import os

import src.config as config


def test_config_env_defaults(monkeypatch):
    with monkeypatch.context() as env:
        env.delenv("ASR_BACKEND", raising=False)
        env.delenv("ASR_ALLOW_FALLBACK", raising=False)
        env.delenv("ASR_SEGMENTATION_MODE", raising=False)
        env.delenv("ASR_VAD_DEVICE", raising=False)
        importlib.reload(config)

        assert config.ASR_BACKEND == "auto"
        assert config.ASR_ALLOW_FALLBACK is True
        assert config.ASR_SEGMENTATION_MODE == "vad"
        assert config.ASR_VAD_DEVICE == "cpu"
    importlib.reload(config)
    assert config.ASR_SEGMENTATION_MODE == os.getenv("ASR_SEGMENTATION_MODE", "vad").strip().lower()
    assert config.ASR_VAD_DEVICE == (os.getenv("ASR_VAD_DEVICE", "cpu").strip().lower() or "cpu")


def test_config_parse_bool_false_and_overrides(monkeypatch):
    with monkeypatch.context() as env:
        env.setenv("ASR_BACKEND", "mlx")
        env.setenv("ASR_ALLOW_FALLBACK", "0")
        env.setenv("MLX_MODEL_REPO", "repo/test")
        env.setenv("ASR_SEGMENTATION_MODE", "fixed_chunks")
        env.setenv("ASR_VAD_DEVICE", "cuda")
        importlib.reload(config)

        assert config.ASR_BACKEND == "mlx"
        assert config.ASR_ALLOW_FALLBACK is False
        assert config.MLX_MODEL_REPO == "repo/test"
        assert config.ASR_SEGMENTATION_MODE == "fixed_chunks"
        assert config.ASR_VAD_DEVICE == "cuda"
    importlib.reload(config)
    assert config.ASR_SEGMENTATION_MODE == os.getenv("ASR_SEGMENTATION_MODE", "vad").strip().lower()
    assert config.ASR_VAD_DEVICE == (os.getenv("ASR_VAD_DEVICE", "cpu").strip().lower() or "cpu")


def test_config_accepts_safe_overlap_mode(monkeypatch):
    with monkeypatch.context() as env:
        env.setenv("ASR_SEGMENTATION_MODE", "overlap_chunks")
        importlib.reload(config)
        assert config.ASR_SEGMENTATION_MODE == "overlap_chunks"
    importlib.reload(config)
