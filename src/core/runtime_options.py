"""Допустимые значения настроек ASR без импортов: их читает и src.config.

src.config не может импортировать src.core.asr (пакет тянет backend-ы, а они —
сам config), поэтому списки и проверки живут здесь, в модуле без зависимостей.
Прежде одни и те же множества были выписаны в config, asr/types, ModelLoader и
transcription_service по отдельности.
"""

from __future__ import annotations

ASR_BACKENDS = ("auto", "mlx", "onnx", "pytorch")
ONNX_PROVIDERS = ("auto", "cpu", "cuda", "tensorrt", "coreml", "directml")

_TRUE = {"1", "true", "t", "yes", "y", "on", "enable", "enabled"}
_FALSE = {"0", "false", "f", "no", "n", "off", "disable", "disabled"}


def validate_backend_name(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized not in ASR_BACKENDS:
        raise ValueError(f"Unsupported ASR backend: {normalized}")
    return normalized


def validate_onnx_provider(value: str | None) -> str:
    normalized = (value or "auto").strip().lower() or "auto"
    if normalized not in ONNX_PROVIDERS:
        raise ValueError(f"Unsupported ONNX provider: {normalized}")
    return normalized


def parse_bool(value: str | bool | None, default: bool = False) -> bool:
    """Boolean-like значение из .env: неизвестная строка — ``default``."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    return default
