"""Тесты выбора ASR backend по платформе и policy."""

import pytest

from src.core.asr.factory import create_backend_from_config
from src.core.asr.pytorch_backend import PyTorchBackend


def _ml_import_probe(modules):
    return all(m in modules for m in ("mlx", "gigaam_mlx"))


def test_auto_selects_mlx_on_macos_arm64(monkeypatch):
    backend, reason = create_backend_from_config(
        requested_backend="auto",
        model_name="e2e_rnnt",
        model_revision="e2e_rnnt",
        mlx_model_repo="repo/mlx",
        allow_fallback=False,
        platform_name="darwin",
        machine_name="arm64",
        import_probe=lambda modules: True,
    )
    assert backend.name == "mlx"
    assert reason is None


def test_auto_falls_back_to_pytorch_on_non_macos(monkeypatch):
    backend, reason = create_backend_from_config(
        requested_backend="auto",
        model_name="e2e_rnnt",
        model_revision="e2e_rnnt",
        mlx_model_repo="repo/mlx",
        allow_fallback=False,
        platform_name="linux",
        machine_name="x86_64",
        import_probe=lambda modules: False,
    )
    assert backend.name == "pytorch"
    assert isinstance(backend, PyTorchBackend)
    assert reason is None


def test_auto_selects_pytorch_for_multilingual_model_on_macos():
    backend, reason = create_backend_from_config(
        requested_backend="auto",
        model_name="multilingual_ctc",
        model_revision="multilingual_ctc",
        mlx_model_repo="repo/mlx",
        allow_fallback=False,
        platform_name="darwin",
        machine_name="arm64",
        import_probe=lambda modules: True,
    )

    assert isinstance(backend, PyTorchBackend)
    assert backend.model_revision == "multilingual_ctc"
    assert reason == "Модель multilingual_ctc поддерживается только PyTorch backend"


def test_auto_with_failed_mlx_probe_fallback_reason(monkeypatch):
    backend, reason = create_backend_from_config(
        requested_backend="auto",
        model_name="e2e_rnnt",
        model_revision="e2e_rnnt",
        mlx_model_repo="repo/mlx",
        allow_fallback=True,
        platform_name="darwin",
        machine_name="arm64",
        import_probe=lambda modules: False,
    )
    assert backend.name == "pytorch"
    assert reason is not None


def test_mlx_request_on_non_mac_raises():
    with pytest.raises(RuntimeError):
        create_backend_from_config(
            requested_backend="mlx",
            model_name="e2e_rnnt",
            model_revision="e2e_rnnt",
            mlx_model_repo="repo/mlx",
            allow_fallback=True,
            platform_name="linux",
            machine_name="x86_64",
            import_probe=lambda modules: True,
        )


def test_explicit_mlx_rejects_multilingual_model():
    with pytest.raises(RuntimeError, match="только через PyTorch"):
        create_backend_from_config(
            requested_backend="mlx",
            model_name="multilingual_large_ctc",
            model_revision="multilingual_large_ctc",
            mlx_model_repo="repo/mlx",
            allow_fallback=True,
            platform_name="darwin",
            machine_name="arm64",
            import_probe=lambda modules: True,
        )
