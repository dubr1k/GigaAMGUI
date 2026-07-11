"""Тесты конфигурации ASR backend."""

import importlib

import src.config as config


def test_config_env_defaults(monkeypatch):
    monkeypatch.delenv("ASR_BACKEND", raising=False)
    monkeypatch.delenv("ASR_ALLOW_FALLBACK", raising=False)
    importlib.reload(config)

    assert config.ASR_BACKEND == "auto"
    assert config.ASR_ALLOW_FALLBACK is True


def test_config_parse_bool_false_and_overrides(monkeypatch):
    monkeypatch.setenv("ASR_BACKEND", "mlx")
    monkeypatch.setenv("ASR_ALLOW_FALLBACK", "0")
    monkeypatch.setenv("MLX_MODEL_REPO", "repo/test")
    importlib.reload(config)

    assert config.ASR_BACKEND == "mlx"
    assert config.ASR_ALLOW_FALLBACK is False
    assert config.MLX_MODEL_REPO == "repo/test"
