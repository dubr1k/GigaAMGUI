"""Stable contract shared by legacy and ONNX diarization backends."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class SpeakerSegment:
    start: float
    end: float
    speaker: str

    @property
    def duration(self) -> float:
        return self.end - self.start


@runtime_checkable
class DiarizationBackend(Protocol):
    backend: str

    def diarize(
        self,
        audio_path: str,
        num_speakers: int | None = None,
        progress_callback: Callable | None = None,
    ) -> list[SpeakerSegment]: ...

    def map_speakers_to_transcription(
        self,
        transcription_segments: list,
        speaker_segments: list[SpeakerSegment],
    ) -> list: ...

    def unload(self) -> None: ...
