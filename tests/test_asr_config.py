"""Тесты конфигурации ASR backend."""

import importlib
import os
import sys

import pytest

import src.config as config


def test_config_env_defaults(monkeypatch):
    with monkeypatch.context() as env:
        # Reload заново прогоняет load_env() и bootstrap_data_dir(), а те читают
        # user/project .env и сохранённый GUI-выбор каталога данных. Без изоляции
        # тест проверяет не дефолты, а личную конфигурацию машины, и зеленеет
        # только на чистом CI-раннере.
        import dotenv

        from src import data_paths

        env.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
        env.setattr(data_paths, "load_data_dir_selection", lambda **kwargs: None)
        env.delenv("GIGAAM_DATA_DIR", raising=False)
        env.delenv("GIGAAM_PYTORCH_MODEL_DIR", raising=False)
        env.delenv("ASR_BACKEND", raising=False)
        env.delenv("ASR_ALLOW_FALLBACK", raising=False)
        env.delenv("ASR_SEGMENTATION_MODE", raising=False)
        env.delenv("ASR_VAD_DEVICE", raising=False)
        env.delenv("ONNX_PROVIDER", raising=False)
        env.delenv("ONNX_QUANTIZATION", raising=False)
        env.delenv("ONNX_MODEL_DIR", raising=False)
        env.delenv("ONNX_VAD_MODEL", raising=False)
        importlib.reload(config)

        assert config.ASR_BACKEND == "auto"
        assert config.ASR_ALLOW_FALLBACK is True
        assert config.ASR_SEGMENTATION_MODE == "vad"
        # На Apple Silicon память общая, отдельного бюджета у ускорителя нет:
        # VAD на MPS даёт меньший пик, чем на CPU, и вчетверо меньшее время.
        # На CUDA бюджет VRAM отдельный и ограниченный — там остаётся CPU.
        assert config.ASR_VAD_DEVICE == ("auto" if sys.platform == "darwin" else "cpu")
        assert config.ONNX_PROVIDER == "auto"
        assert config.ONNX_QUANTIZATION is None
        assert config.ONNX_MODEL_DIR is None
        assert config.ONNX_VAD_MODEL == "silero"
    importlib.reload(config)
    assert config.ASR_SEGMENTATION_MODE == os.getenv("ASR_SEGMENTATION_MODE", "vad").strip().lower()
    _vad_default = "auto" if sys.platform == "darwin" else "cpu"
    assert config.ASR_VAD_DEVICE == (
        os.getenv("ASR_VAD_DEVICE", _vad_default).strip().lower() or _vad_default
    )


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
    _vad_default = "auto" if sys.platform == "darwin" else "cpu"
    assert config.ASR_VAD_DEVICE == (
        os.getenv("ASR_VAD_DEVICE", _vad_default).strip().lower() or _vad_default
    )


def test_config_accepts_safe_overlap_mode(monkeypatch):
    with monkeypatch.context() as env:
        env.setenv("ASR_SEGMENTATION_MODE", "overlap_chunks")
        importlib.reload(config)
        assert config.ASR_SEGMENTATION_MODE == "overlap_chunks"
    importlib.reload(config)


def test_config_accepts_onnx_backend_and_runtime_settings(monkeypatch, tmp_path):
    with monkeypatch.context() as env:
        env.setenv("ASR_BACKEND", "onnx")
        env.setenv("ONNX_PROVIDER", "CUDA")
        env.setenv("ONNX_QUANTIZATION", "INT8")
        env.setenv("ONNX_MODEL_DIR", str(tmp_path))
        env.setenv("ONNX_VAD_MODEL", "onnx-community/pyannote-segmentation-3.0")
        importlib.reload(config)

        assert config.ASR_BACKEND == "onnx"
        assert config.ONNX_PROVIDER == "cuda"
        assert config.ONNX_QUANTIZATION == "int8"
        assert config.ONNX_MODEL_DIR == str(tmp_path)
        assert config.ONNX_VAD_MODEL == "onnx-community/pyannote-segmentation-3.0"
    importlib.reload(config)


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("ONNX_PROVIDER", "openvino", "Unsupported ONNX provider"),
        ("ONNX_QUANTIZATION", "fp6", "Unsupported ONNX quantization"),
    ],
)
def test_invalid_onnx_runtime_setting_is_rejected(monkeypatch, name, value, message):
    with monkeypatch.context() as env:
        env.setenv(name, value)
        with pytest.raises(ValueError, match=message):
            importlib.reload(config)
    importlib.reload(config)
