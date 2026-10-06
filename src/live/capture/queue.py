"""Non-blocking bounded queue for copied capture frames."""

from __future__ import annotations

from collections.abc import Callable
from queue import Empty, SimpleQueue
from threading import Lock

from ..types import CaptureEvent, CaptureEventKind, PcmChunk


class BoundedChunkQueue:
    """Bound the backlog by bytes.

    A frame-count bound makes the memory ceiling scale with the channel count:
    the 32-channel PulseAudio aggregate of issue #48 turned the same limit into
    a 32x larger buffer, and the queue overflowed instead of holding the amount
    of audio it was sized for.
    """

    def __init__(
        self,
        max_bytes: int,
        on_event: Callable[[CaptureEvent], None] | None = None,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._max_bytes = max_bytes
        self._on_event = on_event
        self._chunks: SimpleQueue[PcmChunk] = SimpleQueue()
        self._lock = Lock()
        self._queued_bytes = 0
        self.dropped_frames = 0

    def put(self, chunk: PcmChunk) -> bool:
        frame_count = len(chunk.frames)
        chunk_bytes = int(chunk.frames.nbytes)
        with self._lock:
            if self._queued_bytes + chunk_bytes > self._max_bytes:
                self.dropped_frames += frame_count
                accepted = False
            else:
                self._queued_bytes += chunk_bytes
                accepted = True

        if not accepted:
            if self._on_event is not None:
                self._on_event(
                    CaptureEvent(
                        CaptureEventKind.OVERFLOW,
                        chunk.source,
                        chunk.sample_offset,
                        chunk.timestamp_ns,
                        f"queue full; dropped_frames={frame_count}",
                    )
                )
            return False

        self._chunks.put(chunk)
        return True

    def get(self, timeout: float) -> PcmChunk | None:
        try:
            chunk = self._chunks.get(timeout=timeout)
        except Empty:
            return None

        with self._lock:
            self._queued_bytes -= int(chunk.frames.nbytes)
        return chunk
