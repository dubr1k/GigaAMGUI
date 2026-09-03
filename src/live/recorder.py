"""Bounded, source-native session recording."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from .types import CaptureSource, PcmChunk

FLAC_MAX_CHANNELS = 8
"""libsndfile's ceiling for FLAC.

Above it the writer fails with a bare "Format not recognised" that names the
format rather than the channel count, so a backend reporting an implausible
channel count (PulseAudio aggregates advertise 32 — issue #48) costs the whole
recording and misdirects the diagnosis. Extra channels are dropped instead.
"""


class SessionRecorder:
    def __init__(
        self,
        session_dir: Path,
        record_sources: bool | set[CaptureSource] = True,
        record_mix: bool = True,
        writer_factory: Callable[..., Any] = sf.SoundFile,
        *,
        max_block_frames: int = 48_000,
        segment_max_bytes: int = 256 * 1024 * 1024,
        segment_max_duration_seconds: int = 30 * 60,
    ) -> None:
        if max_block_frames <= 0:
            raise ValueError("max_block_frames must be positive")
        if segment_max_bytes <= 0 or segment_max_duration_seconds <= 0:
            raise ValueError("segment limits must be positive")
        self._session_dir = Path(session_dir)
        self._record_sources = record_sources
        self._record_mix = record_mix
        self._writer_factory = writer_factory
        self._max_block_frames = max_block_frames
        self._segment_max_bytes = segment_max_bytes
        self._segment_max_duration_seconds = segment_max_duration_seconds
        self._writers: dict[CaptureSource | str, Any] = {}
        self._paths: dict[CaptureSource | str, list[Path]] = {}
        self._segment_frames: dict[CaptureSource | str, int] = {}
        self._total_frames: dict[CaptureSource | str, int] = {}
        self._total_bytes: dict[CaptureSource | str, int] = {}
        self._formats: dict[CaptureSource | str, tuple[int, int]] = {}
        self._segments: dict[CaptureSource | str, list[dict[str, int | str]]] = {}

    def write(self, chunk: PcmChunk) -> None:
        if self._record_sources is True or (
            isinstance(self._record_sources, set) and chunk.source in self._record_sources
        ):
            self._write(chunk.source, chunk)

    def write_mix(self, chunk: PcmChunk) -> None:
        if self._record_mix:
            self._write("mix", chunk)

    def close(self) -> dict[CaptureSource, Path]:
        for writer in self._writers.values():
            writer.close()
        return {
            source: paths[0]
            for source, paths in self._paths.items()
            if isinstance(source, CaptureSource) and paths
        }

    def artifacts(self) -> dict[str, dict[str, Any]]:
        return {
            source.value if isinstance(source, CaptureSource) else source: {
                "paths": [str(path) for path in paths],
                "codec": "FLAC PCM_24",
                "rate": self._formats[source][0],
                "channels": self._formats[source][1],
                "frames": self._total_frames[source],
                "bytes": self._total_bytes[source],
                "segments": self._segments[source],
            }
            for source, paths in self._paths.items()
        }

    def _write(self, track: CaptureSource | str, chunk: PcmChunk) -> None:
        if len(chunk.frames) > self._max_block_frames:
            raise ValueError("recording block exceeds frame limit")
        chunk = self._clamp_channels(chunk)
        format_info = self._formats.get(track)
        if format_info is not None and format_info != (chunk.sample_rate, chunk.channels):
            raise ValueError("recording track format changed")
        self._formats.setdefault(track, (chunk.sample_rate, chunk.channels))
        frame_bytes = chunk.channels * 3
        remaining = len(chunk.frames)
        start = 0
        while remaining:
            writer = self._writers.get(track)
            if writer is None or self._segment_frames[track] >= self._segment_limit_frames(chunk):
                if writer is not None:
                    writer.close()
                self._open_segment(track, chunk)
                writer = self._writers[track]
            writable = min(remaining, self._segment_limit_frames(chunk) - self._segment_frames[track])
            writer.write(chunk.frames[start:start + writable])
            self._segment_frames[track] += writable
            self._total_frames[track] += writable
            self._total_bytes[track] += writable * frame_bytes
            self._segments[track][-1]["frames"] += writable
            self._segments[track][-1]["bytes"] += writable * frame_bytes
            start += writable
            remaining -= writable

    @staticmethod
    def _clamp_channels(chunk: PcmChunk) -> PcmChunk:
        if chunk.channels <= FLAC_MAX_CHANNELS:
            return chunk
        return PcmChunk(
            chunk.source,
            chunk.sample_rate,
            FLAC_MAX_CHANNELS,
            chunk.sample_offset,
            np.ascontiguousarray(chunk.frames[:, :FLAC_MAX_CHANNELS]),
            chunk.timestamp_ns,
        )

    def _open_segment(self, track: CaptureSource | str, chunk: PcmChunk) -> None:
        self._session_dir.mkdir(parents=True, exist_ok=True)
        paths = self._paths.setdefault(track, [])
        name = track.value if isinstance(track, CaptureSource) else track
        suffix = "" if not paths else f"-{len(paths) + 1:03d}"
        path = self._session_dir / f"{name}{suffix}.flac"
        self._writers[track] = self._writer_factory(
            path,
            mode="w",
            samplerate=chunk.sample_rate,
            channels=chunk.channels,
            format="FLAC",
            subtype="PCM_24",
        )
        paths.append(path)
        self._segments.setdefault(track, []).append({"path": str(path), "frames": 0, "bytes": 0})
        self._segment_frames[track] = 0
        self._total_frames.setdefault(track, 0)
        self._total_bytes.setdefault(track, 0)

    def _segment_limit_frames(self, chunk: PcmChunk) -> int:
        bytes_limit = self._segment_max_bytes // (chunk.channels * 3)
        duration_limit = chunk.sample_rate * self._segment_max_duration_seconds
        return max(1, min(bytes_limit, duration_limit))
