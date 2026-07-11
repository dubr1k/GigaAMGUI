"""Shared ASR segment and backend metadata types."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypedDict


class TranscriptionSegment(TypedDict):
    """Single transcription result used across all ASR backends."""

    transcription: str
    boundaries: tuple[float, float]


@dataclass(frozen=True)
class BackendCapabilities:
    """Runtime backend metadata for diagnostics."""

    backend: str
    model: str
    device: str
    supports_local_asr: bool = True


def validate_backend_name(value: str) -> str:
    value = (value or "").strip().lower()
    if value not in {"auto", "mlx", "pytorch"}:
        raise ValueError(f"Unsupported ASR backend: {value}")
    return value


def parse_bool(value: str | bool | None, default: bool = False) -> bool:
    """Parse boolean-like env values used by config."""

    if isinstance(value, bool):
        return value
    if value is None:
        return default

    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "t", "yes", "y", "on", "enable", "enabled"}:
        return True
    if normalized in {"0", "false", "f", "no", "n", "off", "disable", "disabled"}:
        return False
    return default

ProgressCallback = Callable[[float, float | None, float | None], None]
