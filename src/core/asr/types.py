"""Shared ASR segment and backend metadata types."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from math import gcd
from typing import TypedDict

import numpy as np
from typing_extensions import NotRequired


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
    return resample_window(samples[:, None], sample_rate, target_rate)[:, 0]


# Resampling: one polyphase windowed-sinc filter for whole windows and for
# live streams. Linear interpolation had no anti-aliasing (a 10 kHz tone at
# 48 kHz came out of 48→16 kHz as a 6 kHz tone at most of its level), and
# applied per live chunk with endpoint-inclusive positions it also warped
# every chunk boundary.
_RESAMPLE_ZERO_CROSSINGS = 16
"""Sinc zero crossings per side, counted at the lower of the two rates."""
_RESAMPLE_ROLLOFF = 0.9
"""Cutoff as a fraction of the lower Nyquist frequency."""
_RESAMPLE_KAISER_BETA = 8.0
_RESAMPLE_BLOCK_FRAMES = 16_384


@lru_cache(maxsize=16)
def _polyphase_bank(up: int, down: int) -> tuple[np.ndarray, int]:
    """Filter bank (phases × taps) and its centre, in upsampled samples."""
    half = _RESAMPLE_ZERO_CROSSINGS * max(up, down)
    length = 2 * half + 1
    cutoff = _RESAMPLE_ROLLOFF * 0.5 / max(up, down)
    positions = np.arange(length) - half
    prototype = 2 * cutoff * np.sinc(2 * cutoff * positions) * np.kaiser(length, _RESAMPLE_KAISER_BETA)
    taps = -(-length // up)
    padded = np.zeros(taps * up)
    padded[:length] = prototype
    bank = padded.reshape(taps, up).T
    # Every phase passes DC unchanged, so silence and offsets stay exact.
    bank = bank / bank.sum(axis=1, keepdims=True)
    return np.ascontiguousarray(bank, dtype=np.float32), half


class StreamResampler:
    """Rational-ratio resampler that keeps its filter state between chunks.

    Fed chunk by chunk it produces exactly what one call over the whole
    signal would, so chunk boundaries leave no trace. The output trails the
    input by the filter's half length (about 1 ms); the stream starts as if
    the first sample had always been there, so it opens without a click.
    Frames are (count, channels).
    """

    def __init__(self, source_rate: int, target_rate: int) -> None:
        if source_rate <= 0 or target_rate <= 0:
            raise ValueError("sample rates must be positive")
        common = gcd(source_rate, target_rate)
        self._up = target_rate // common
        self._down = source_rate // common
        self._bank, self._half = _polyphase_bank(self._up, self._down)
        self._history: np.ndarray | None = None
        self._position = 0

    @property
    def delay_frames(self) -> float:
        """How far the output trails the input, in output frames."""
        return self._half / self._down

    def process(self, frames: np.ndarray) -> np.ndarray:
        frames = np.asarray(frames, dtype=np.float32)
        if frames.ndim != 2:
            raise ValueError("frames must have shape (frame_count, channels)")
        if not len(frames):
            return np.zeros((0, frames.shape[1]), dtype=np.float32)
        if len(frames) > _RESAMPLE_BLOCK_FRAMES:
            # The gathered filter windows grow as outputs × taps; a long
            # window in one piece would need hundreds of megabytes.
            return np.concatenate([
                self.process(frames[start:start + _RESAMPLE_BLOCK_FRAMES])
                for start in range(0, len(frames), _RESAMPLE_BLOCK_FRAMES)
            ])
        taps = self._bank.shape[1]
        if self._history is None or self._history.shape[1] != frames.shape[1]:
            self._history = np.repeat(frames[:1], taps - 1, axis=0)
            self._position = (taps - 1) * self._up
        signal = np.concatenate((self._history, frames))
        positions = np.arange(self._position, len(signal) * self._up, self._down)
        newest = positions // self._up
        window = signal[newest[:, None] - np.arange(taps)[None, :]]
        output = np.einsum("ot,otc->oc", self._bank[positions % self._up], window)
        consumed = len(signal) - (taps - 1)
        next_position = positions[-1] + self._down if len(positions) else self._position
        self._history = signal[consumed:]
        self._position = int(next_position) - consumed * self._up
        return output.astype(np.float32, copy=False)


def resample_window(frames: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """Resample a complete window without delay: round(n·target/source) frames."""
    frames = np.asarray(frames, dtype=np.float32)
    expected = round(len(frames) * target_rate / source_rate)
    if source_rate == target_rate or not len(frames):
        return frames.copy()
    resampler = StreamResampler(source_rate, target_rate)
    # Hold the last sample long enough for the filter to reach the end, then
    # drop the filter delay from the front.
    held = int(np.ceil(resampler.delay_frames * source_rate / target_rate)) + 2
    output = resampler.process(np.concatenate((frames, np.repeat(frames[-1:], held, axis=0))))
    start = round(resampler.delay_frames)
    return output[start:start + expected]


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


def validate_backend_name(value: str) -> str:
    value = (value or "").strip().lower()
    if value not in {"auto", "mlx", "onnx", "pytorch"}:
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
