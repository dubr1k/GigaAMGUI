"""Capture adapter fed by an external process (the Swift client pushes PCM)."""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock

import numpy as np

from ..types import CaptureDevice, CaptureEvent, CaptureEventKind, CaptureSource, PcmChunk


class PushCaptureAdapter:
    """`CaptureAdapter` whose audio arrives via `push()` instead of a device callback.

    The pushing side owns the hardware and its permissions; this adapter only
    validates ordering and converts frames into `PcmChunk`.
    """

    def __init__(self, source: CaptureSource, sample_rate: int = 16_000, channels: int = 1) -> None:
        self.source = source
        self.sample_rate = sample_rate
        self.channels = channels
        self._on_chunk: Callable[[PcmChunk], None] | None = None
        self._on_event: Callable[[CaptureEvent], None] | None = None
        self._started = False
        self._paused = False
        self._next_seq = 0
        self._lock = Lock()

    def start(
        self,
        on_chunk: Callable[[PcmChunk], None],
        on_event: Callable[[CaptureEvent], None],
    ) -> None:
        with self._lock:
            self._on_chunk = on_chunk
            self._on_event = on_event
            self._started = True
            self._paused = False
            self._next_seq = 0

    def pause(self) -> None:
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            if self._started:
                self._paused = False

    def stop(self) -> None:
        with self._lock:
            self._started = False
            self._paused = False
            self._on_chunk = None
            self._on_event = None

    def devices(self) -> list[CaptureDevice]:
        name = "Microphone (native client)" if self.source is CaptureSource.MIC else "System audio (native client)"
        return [CaptureDevice(f"{self.source.value}-push", name, self.source, self.sample_rate, self.channels, True)]

    def push(self, seq: int, sample_offset: int, timestamp_ns: int, frames: np.ndarray) -> None:
        with self._lock:
            if not self._started or self._paused or self._on_chunk is None:
                return
            on_chunk, on_event = self._on_chunk, self._on_event
            expected = self._next_seq
            self._next_seq = seq + 1
        if seq != expected and on_event is not None:
            on_event(CaptureEvent(
                CaptureEventKind.DISCONTINUITY, self.source, sample_offset, timestamp_ns,
                f"seq gap: expected {expected}, got {seq}",
            ))
        frames = np.ascontiguousarray(frames, dtype=np.float32).reshape(-1, self.channels).copy()
        on_chunk(PcmChunk(self.source, self.sample_rate, self.channels, sample_offset, frames, timestamp_ns))

    def event(self, kind: CaptureEventKind, detail: str) -> None:
        with self._lock:
            on_event = self._on_event
        if on_event is not None:
            on_event(CaptureEvent(kind, self.source, 0, 0, detail))
