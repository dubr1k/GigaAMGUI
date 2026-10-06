"""Тесты выбора ASR backend по платформе и policy."""

import pytest

from src.core.asr.factory import create_backend_from_config
from src.core.asr.onnx_backend import OnnxBackend
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


def test_auto_keeps_pytorch_on_non_macos_until_quality_gate_passes():
    backend, reason = create_backend_from_config(
        requested_backend="auto",
        model_name="v3_e2e_rnnt",
        model_revision="v3_e2e_rnnt",
        mlx_model_repo="repo/mlx",
        allow_fallback=True,
        platform_name="linux",
        machine_name="x86_64",
        import_probe=lambda modules: modules == ("onnx_asr", "onnxruntime"),
    )

    assert isinstance(backend, PyTorchBackend)
    assert reason is None


def test_explicit_onnx_creates_onnx_backend_without_import_probe():
    backend, reason = create_backend_from_config(
        requested_backend="onnx",
        model_name="v3_e2e_rnnt",
        model_revision="v3_e2e_rnnt",
        mlx_model_repo="repo/mlx",
        allow_fallback=False,
        platform_name="linux",
        machine_name="x86_64",
        import_probe=lambda modules: False,
    )

    assert isinstance(backend, OnnxBackend)
    assert backend.model_revision == "v3_e2e_rnnt"
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


def _auto_arm64(**overrides):
    kwargs = dict(
        requested_backend="auto",
        model_name="v3_e2e_rnnt",
        model_revision="v3_e2e_rnnt",
        mlx_model_repo="repo/mlx",
        allow_fallback=True,
        platform_name="darwin",
        machine_name="arm64",
        import_probe=lambda modules: True,
    )
    kwargs.update(overrides)
    return create_backend_from_config(**kwargs)


def test_auto_offline_prefers_bundled_onnx_when_mlx_model_is_not_cached(monkeypatch):
    # Офлайн-архив Liquid везёт только ONNX-цепочку и запускает companion с
    # HF_HUB_OFFLINE=1: MLX-модель докачать нельзя, а PyTorch-fallback офлайн
    # мёртв так же — auto обязан взять ONNX, раз его модель лежит в кэше.
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    backend, reason = _auto_arm64(repo_is_cached=lambda repo: repo != "repo/mlx")

    assert isinstance(backend, OnnxBackend)
    assert backend.model_revision == "v3_e2e_rnnt"
    assert reason is not None and "repo/mlx" in reason and "ONNX" in reason


def test_auto_offline_keeps_mlx_when_its_model_is_cached(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    backend, reason = _auto_arm64(repo_is_cached=lambda repo: True)

    assert backend.name == "mlx"
    assert reason is None


def test_auto_offline_keeps_mlx_when_nothing_is_cached(monkeypatch):
    # Нечего предпочесть — пусть MLX упадёт своим понятным сообщением о модели.
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    backend, reason = _auto_arm64(repo_is_cached=lambda repo: False)

    assert backend.name == "mlx"
    assert reason is None


def test_auto_online_ignores_cache_state(monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    backend, reason = _auto_arm64(repo_is_cached=lambda repo: False)

    assert backend.name == "mlx"
    assert reason is None


def test_auto_selects_onnx_on_macos_x86_64():
    # Под macOS x86_64 нет ни колёс torch>=2.6, ни mlx: PyTorch-ветка там не
    # медленнее, а мертва — падает с «No module named 'gigaam'» (issue #45).
    backend, reason = create_backend_from_config(
        requested_backend="auto",
        model_name="e2e_rnnt",
        model_revision="v3_e2e_rnnt",
        mlx_model_repo="repo/mlx",
        allow_fallback=True,
        platform_name="darwin",
        machine_name="x86_64",
        import_probe=lambda modules: False,
    )
    assert isinstance(backend, OnnxBackend)
    assert backend.name == "onnx"
    assert reason is not None and "x86_64" in reason
