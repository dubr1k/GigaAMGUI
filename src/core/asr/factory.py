"""Factory utilities for ASR backend selection and fallback."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .mlx_backend import MLXBackend
from .onnx_backend import OnnxBackend
from .pytorch_backend import PyTorchBackend
from .types import validate_backend_name


def _is_macos_arm64(platform_name: str, machine_name: str) -> bool:
    return platform_name == "darwin" and machine_name == "arm64"


def _default_import_probe(modules: tuple[str, ...]) -> bool:
    try:
        for module in modules:
            __import__(module)
        return True
    except Exception:
        return False


def _mlx_supports_model(model_revision: str) -> bool:
    return model_revision.strip().lower() in {
        "rnnt",
        "e2e_rnnt",
        "v3_e2e_rnnt",
    }


def create_backend(
    requested_backend: str,
    *,
    model_name: str,
    model_revision: str,
    mlx_repo: str,
    allow_fallback: bool,
    onnx_provider: str = "auto",
    onnx_quantization: str | None = None,
    onnx_model_dir: str | None = None,
    onnx_vad_model: str = "silero",
    platform_name: str = __import__("sys").platform,
    machine_name: str = __import__("platform").machine(),
    import_probe: Callable[[tuple[str, ...]], bool] = _default_import_probe,
) -> tuple[Any, str | None]:
    requested = validate_backend_name(requested_backend)

    if requested == "pytorch":
        return PyTorchBackend(model=model_name, revision=model_revision), None

    if requested == "onnx":
        return OnnxBackend(
            model=model_revision,
            provider=onnx_provider,
            quantization=onnx_quantization,
            model_dir=onnx_model_dir,
            vad_model=onnx_vad_model,
        ), None

    if requested == "mlx":
        if not _is_macos_arm64(platform_name, machine_name):
            raise RuntimeError(
                "Явное API mlx доступно только на macOS Apple Silicon (darwin/arm64)"
            )
        if not _mlx_supports_model(model_revision):
            raise RuntimeError(
                f"Модель {model_revision} доступна только через PyTorch backend"
            )
        if not import_probe(("mlx", "gigaam_mlx")):
            raise RuntimeError(
                "Модуль mlx/gigaam_mlx недоступен. Установите зависимость gigaam-mlx для MLX backend."
            )
        return MLXBackend(model=model_name, repo=mlx_repo), None

    # requested == "auto"
    if not _is_macos_arm64(platform_name, machine_name):
        # ONNX remains opt-in until the local WER/CER release corpus passes.
        return PyTorchBackend(model=model_name, revision=model_revision), None

    if not _mlx_supports_model(model_revision):
        return (
            PyTorchBackend(model=model_name, revision=model_revision),
            f"Модель {model_revision} поддерживается только PyTorch backend",
        )

    if import_probe(("mlx", "gigaam_mlx")):
        return MLXBackend(model=model_name, repo=mlx_repo), None

    if allow_fallback:
        return PyTorchBackend(model=model_name, revision=model_revision), "MLX недоступен (mlx/gigaam_mlx не импортируются), использован fallback PyTorch"

    raise RuntimeError("MLX backend недоступен для auto-настройки на этой платформе")


def create_backend_from_config(
    *,
    requested_backend: str,
    model_name: str,
    model_revision: str,
    mlx_model_repo: str,
    allow_fallback: bool,
    onnx_provider: str = "auto",
    onnx_quantization: str | None = None,
    onnx_model_dir: str | None = None,
    onnx_vad_model: str = "silero",
    platform_name: str = __import__("sys").platform,
    machine_name: str = __import__("platform").machine(),
    import_probe: Callable[[tuple[str, ...]], bool] = _default_import_probe,
):
    return create_backend(
        requested_backend=requested_backend,
        model_name=model_name,
        model_revision=model_revision,
        mlx_repo=mlx_model_repo,
        allow_fallback=allow_fallback,
        onnx_provider=onnx_provider,
        onnx_quantization=onnx_quantization,
        onnx_model_dir=onnx_model_dir,
        onnx_vad_model=onnx_vad_model,
        platform_name=platform_name,
        machine_name=machine_name,
        import_probe=import_probe,
    )
