"""Shared ASR segment and backend metadata types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

import numpy as np
from typing_extensions import NotRequired

from ..progress import ProgressCallback  # noqa: F401 — публичный реэкспорт
from ..runtime_options import parse_bool, validate_backend_name  # noqa: F401 — публичный реэкспорт


class TranscriptionWord(TypedDict):
    """One recognized word with absolute audio timestamps."""

    text: str
    start: float
    end: float


class TranscriptionSegment(TypedDict):
    """Single transcription result used across all ASR backends."""

    transcription: str
    boundaries: tuple[float, float]
    words: NotRequired[list[TranscriptionWord]]


@dataclass(frozen=True)
class WindowTranscriptionRequest:
    """In-memory audio window positioned on its source timeline."""

    audio: np.ndarray
    sample_rate: int
    offset_samples: int

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if self.offset_samples < 0:
            raise ValueError("offset_samples must be non-negative")
        if self.audio.dtype != np.float32 or self.audio.ndim != 1:
            raise ValueError("audio must be a one-dimensional float32 array")


def normalize_window_audio(audio: np.ndarray, sample_rate: int, target_rate: int = 16_000) -> np.ndarray:
    """Convert an in-memory window to mono float32 at the model sample rate."""
    if sample_rate <= 0 or target_rate <= 0:
        raise ValueError("sample rates must be positive")
    samples = np.asarray(audio, dtype=np.float32)
    if samples.ndim == 2:
        samples = samples.mean(axis=1, dtype=np.float32)
    if samples.ndim != 1:
        raise ValueError("audio must have one channel or a frame/channel shape")
    if sample_rate == target_rate or not len(samples):
        return samples.copy()
    output_size = round(len(samples) * target_rate / sample_rate)
    positions = np.linspace(0, len(samples) - 1, output_size, dtype=np.float32)
    return np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)


@dataclass(frozen=True)
class BackendCapabilities:
    """Runtime backend metadata for diagnostics."""

    backend: str
    model: str
    device: str
    supports_local_asr: bool = True
    segmentation_mode: str | None = None
    segmentation_fallback_reason: str | None = None
    provider: str | None = None
    quantization: str | None = None
    provider_fallback_reason: str | None = None


