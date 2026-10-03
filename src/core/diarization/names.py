"""Имена backend-ов диаризации без импорта тяжёлых зависимостей."""

from __future__ import annotations

DIARIZATION_BACKENDS = ("pyannote", "sortformer", "onnx")
_DIARIZATION_BACKEND_ALIASES = {
    "pyannote": "pyannote",
    "sortformer": "sortformer",
    "nvidia": "sortformer",
    "onnx": "onnx",
}


def normalize_diarization_backend(backend: str | None) -> str:
    """Возвращает каноническое имя backend диаризации."""
    normalized = str(backend or "pyannote").strip().lower()
    try:
        return _DIARIZATION_BACKEND_ALIASES[normalized]
    except KeyError as exc:
        supported = ", ".join(DIARIZATION_BACKENDS)
        raise ValueError(
            f"Неизвестный backend диаризации: {backend!r}. Доступно: {supported}"
        ) from exc
