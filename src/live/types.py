"""Stable live-transcription types shared by platform and UI layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np


class CaptureSource(str, Enum):
    MIC = "mic"
    SYSTEM = "system"


class CaptureState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class DiarizationMode(str, Enum):
    OFF = "off"
    LIVE_ESTIMATE = "live_estimate"
    AFTER_STOP = "after_stop"


class CaptureEventKind(str, Enum):
    PERMISSION_DENIED = "permission_denied"
    DEVICE_REMOVED = "device_removed"
    OVERFLOW = "overflow"
    DISCONTINUITY = "discontinuity"
    DISK_FULL = "disk_full"
    STATUS = "status"


@dataclass(frozen=True)
class CaptureDevice:
    id: str
    name: str
    source: CaptureSource
    sample_rate: int
    channels: int
    is_default: bool


@dataclass(frozen=True)
class CaptureEvent:
    kind: CaptureEventKind
    source: CaptureSource
    sample_offset: int
    timestamp_ns: int
    detail: str


@dataclass(frozen=True)
class PcmChunk:
    source: CaptureSource
    sample_rate: int
    channels: int
    sample_offset: int
    frames: np.ndarray
    timestamp_ns: int

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if self.channels <= 0:
            raise ValueError("channels must be positive")
        if self.sample_offset < 0:
            raise ValueError("sample_offset must be non-negative")
        if self.frames.dtype != np.float32:
            raise ValueError("frames must have dtype float32")
        if self.frames.ndim != 2 or self.frames.shape[1] != self.channels:
            raise ValueError("frames must have shape (frame_count, channels)")
        if not self.frames.flags["OWNDATA"]:
            raise ValueError("frames must own their memory")

        frames = self.frames.copy()
        frames.setflags(write=False)
        object.__setattr__(self, "frames", frames)


@dataclass(frozen=True)
class LiveSettings:
    mic_device_id: str | None = None
    system_device_id: str | None = None
    diarization_mode: DiarizationMode = DiarizationMode.OFF
    diarization_backend: str = "onnx"
    """Backend for after-stop diarization (pyannote, onnx or sortformer)."""
    source_sample_rate: int = 48_000
    asr_sample_rate: int = 16_000
    record_source_audio: bool = True
    record_mic_audio: bool = True
    record_system_audio: bool = True
    record_mix_audio: bool = True


@dataclass(frozen=True)
class TranscriptEvent:
    event_id: str
    revision: int
    source: CaptureSource
    sample_start: int
    sample_end: int
    timestamp_ns: int
    text: str
    status: str
    speaker: str | None = None
    supersedes: int | None = None
    paragraph_break_after: bool = False
    source_label: str = field(init=False)

    def __post_init__(self) -> None:
        if self.revision < 0:
            raise ValueError("revision must be non-negative")
        if self.sample_start < 0 or self.sample_end < self.sample_start:
            raise ValueError("sample offsets must be non-negative and ordered")
        object.__setattr__(self, "source_label", self.source.name)
