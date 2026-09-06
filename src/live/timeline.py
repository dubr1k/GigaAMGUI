"""Source-offset alignment and bounded gain mixing for live audio."""

from __future__ import annotations

from collections.abc import Callable, Mapping

import numpy as np

from .types import CaptureEvent, CaptureEventKind, CaptureSource, PcmChunk


class SourceTimeline:
    def __init__(
        self,
        source: CaptureSource,
        sample_rate: int,
        channels: int,
        on_event: Callable[[CaptureEvent], None] | None = None,
        max_gap_seconds: float = 1.0,
    ) -> None:
        if max_gap_seconds < 0:
            raise ValueError("max_gap_seconds must be non-negative")
        self._source = source
        self._sample_rate = sample_rate
        self._channels = channels
        self._on_event = on_event
        self._max_gap_frames = round(max_gap_seconds * sample_rate)
        self._next_offset: int | None = None

    def ingest(self, chunk: PcmChunk) -> list[PcmChunk]:
        if chunk.source is not self._source:
            raise ValueError("chunk source does not match timeline")
        if chunk.sample_rate != self._sample_rate or chunk.channels != self._channels:
            raise ValueError("chunk format does not match timeline")
        if self._next_offset is None:
            self._next_offset = chunk.sample_offset

        emitted: list[PcmChunk] = []
        if chunk.sample_offset > self._next_offset:
            gap = chunk.sample_offset - self._next_offset
            if gap <= self._max_gap_frames:
                emitted.append(
                    PcmChunk(
                        self._source,
                        self._sample_rate,
                        self._channels,
                        self._next_offset,
                        np.zeros((gap, self._channels), dtype=np.float32),
                        chunk.timestamp_ns,
                    )
                )
                self._emit_discontinuity(chunk, f"gap={gap}")
            else:
                self._emit_discontinuity(chunk, f"gap={gap} discarded")
            self._next_offset = chunk.sample_offset
        elif chunk.sample_offset < self._next_offset:
            overlap = self._next_offset - chunk.sample_offset
            self._emit_discontinuity(chunk, f"overlap={overlap}")
            if overlap >= len(chunk.frames):
                return emitted
            chunk = PcmChunk(
                chunk.source,
                chunk.sample_rate,
                chunk.channels,
                self._next_offset,
                chunk.frames[overlap:].copy(),
                chunk.timestamp_ns,
            )

        emitted.append(chunk)
        self._next_offset += len(chunk.frames)
        return emitted

    def _emit_discontinuity(self, chunk: PcmChunk, detail: str) -> None:
        if self._on_event is not None:
            self._on_event(
                CaptureEvent(
                    CaptureEventKind.DISCONTINUITY,
                    self._source,
                    chunk.sample_offset,
                    chunk.timestamp_ns,
                    detail,
                )
            )


class AlignedMixer:
    """Mixes paired source chunks onto one continuous output timeline.

    Each chunk is placed at its absolute position on that timeline, but the
    output only ever advances by the audio it actually emits: a source that
    runs a constant offset behind its peer costs one block of leading silence
    at the start of the session, not a block of padding per pair.

    Sizing every block as ``offset + length`` instead is what produced issue
    #50 — a 98.6 s session written as a 480.6 s ``mix.flac`` (x4.87) in which
    the microphone, spread across ever-growing runs of silence, was inaudible.
    The offset there was only ~90 ms per pair; paid once per pair for 5 300
    pairs it became almost the whole file.

    Audio that arrives while its peer is still behind is held in ``_pending``
    and emitted once the peer catches up, so nothing is dropped and nothing is
    written twice.
    """

    def __init__(
        self,
        mic_gain: float = 1.0,
        system_gain: float = 1.0,
        *,
        max_skew_seconds: float = 1.0,
        max_output_frames: int = 48_000,
    ) -> None:
        if max_skew_seconds < 0:
            raise ValueError("max_skew_seconds must be non-negative")
        if max_output_frames <= 0:
            raise ValueError("max_output_frames must be positive")
        self._mic_gain = mic_gain
        self._system_gain = system_gain
        self._max_skew_seconds = max_skew_seconds
        self._max_output_frames = max_output_frames
        self._origin_ns: int | None = None
        self._sample_rate: int | None = None
        self._channels: int | None = None
        self._written_frames = 0
        self._pending = np.zeros((0, 1), dtype=np.float32)

    def mix(self, chunks: Mapping[CaptureSource, PcmChunk]) -> PcmChunk:
        if not chunks:
            raise ValueError("at least one chunk is required")
        start_ns = min(chunk.timestamp_ns for chunk in chunks.values())
        skew_seconds = (max(chunk.timestamp_ns for chunk in chunks.values()) - start_ns) / 1_000_000_000
        if skew_seconds > self._max_skew_seconds:
            raise ValueError(f"timestamp skew {skew_seconds:.3f}s exceeds mix limit")
        rate, channels = self._output_format(chunks)
        if self._origin_ns is None:
            self._origin_ns = start_ns
        expected_frames = (
            round(len(chunk.frames) * rate / chunk.sample_rate) for chunk in chunks.values()
        )
        if any(frame_count > self._max_output_frames for frame_count in expected_frames):
            raise ValueError("mix input exceeds output frame limit")
        gains = {CaptureSource.MIC: self._mic_gain, CaptureSource.SYSTEM: self._system_gain}
        placed: list[tuple[int, np.ndarray, float]] = []
        for source, chunk in chunks.items():
            frames = self._normalize(chunk, rate, channels)
            start = round((chunk.timestamp_ns - self._origin_ns) * rate / 1_000_000_000) - self._written_frames
            if start < 0:
                # Only reachable if a source jumps backwards past audio already
                # written; the overlap cannot be un-written, so drop it.
                frames = frames[-start:]
                start = 0
            placed.append((start, frames, gains.get(source, 1.0)))
        required = max(start + len(frames) for start, frames, _ in placed)
        if required > self._max_output_frames:
            raise ValueError("mix output exceeds frame limit")
        self._grow_pending(required, channels)
        for start, frames, gain in placed:
            if len(frames):
                self._pending[start:start + len(frames)] += gain * frames
        # Emitting only as far as the *shortest* placed chunk reaches keeps
        # every later chunk writable: nothing lands in an already-written span.
        return self._emit(min(start + len(frames) for start, frames, _ in placed))

    def flush(self) -> PcmChunk | None:
        """Audio held back waiting for a peer that will not arrive."""
        if self._sample_rate is None or not len(self._pending):
            return None
        return self._emit(len(self._pending))

    def _output_format(self, chunks: Mapping[CaptureSource, PcmChunk]) -> tuple[int, int]:
        """The mix track's format, fixed by the first block.

        A FLAC track cannot change rate mid-file, so the format must not follow
        whichever source happens to be present in a block: a quiet microphone
        would otherwise hand the mix over to the 48 kHz system source and cost
        the whole track.
        """
        if self._sample_rate is None:
            reference = (
                chunks.get(CaptureSource.MIC)
                or chunks.get(CaptureSource.SYSTEM)
                or next(iter(chunks.values()))
            )
            self._sample_rate = reference.sample_rate
            self._channels = reference.channels
            self._pending = np.zeros((0, self._channels), dtype=np.float32)
        return self._sample_rate, self._channels  # type: ignore[return-value]

    def _grow_pending(self, frame_count: int, channels: int) -> None:
        if frame_count > len(self._pending):
            self._pending = np.concatenate(
                (self._pending, np.zeros((frame_count - len(self._pending), channels), dtype=np.float32))
            )

    def _emit(self, frame_count: int) -> PcmChunk:
        frame_count = max(0, min(frame_count, len(self._pending)))
        frames = self._pending[:frame_count].copy()
        np.clip(frames, -1.0, 1.0, out=frames)
        self._pending = self._pending[frame_count:].copy()
        offset = self._written_frames
        self._written_frames += frame_count
        sample_rate = self._sample_rate or 1
        return PcmChunk(
            CaptureSource.MIC,
            sample_rate,
            self._channels or 1,
            offset,
            frames,
            (self._origin_ns or 0) + round(offset * 1_000_000_000 / sample_rate),
        )

    @staticmethod
    def _normalize(chunk: PcmChunk, sample_rate: int, channels: int) -> np.ndarray:
        frames = chunk.frames
        if chunk.channels != channels:
            if channels == 1:
                frames = frames.mean(axis=1, dtype=np.float32)[:, None]
            elif chunk.channels == 1:
                frames = np.repeat(frames, channels, axis=1)
            elif chunk.channels > channels:
                frames = frames[:, :channels]
            else:
                frames = np.concatenate(
                    (frames, np.repeat(frames[:, -1:], channels - chunk.channels, axis=1)), axis=1
                )
        if chunk.sample_rate == sample_rate or not len(frames):
            return frames.copy()
        frame_count = round(len(frames) * sample_rate / chunk.sample_rate)
        positions = np.linspace(0, len(frames) - 1, frame_count, dtype=np.float32)
        return np.stack(
            [np.interp(positions, np.arange(len(frames)), frames[:, channel]) for channel in range(channels)],
            axis=1,
        ).astype(np.float32)
