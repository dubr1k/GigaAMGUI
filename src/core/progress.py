"""Shared progress event primitives for processing workflows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

ProgressStage = Literal[
    "preparing", "conversion", "preprocessing", "transcription", "diarization", "export", "finalizing"
]


ProgressCallback = Callable[[float, float | None, float | None], None]


@dataclass(frozen=True)
class ProgressEvent:
    """Single normalized progress snapshot."""

    stage: ProgressStage
    stage_progress: float | None
    file_progress: float
    processed_seconds: float | None = None
    total_seconds: float | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        if self.stage not in _ALL_STAGES:
            raise ValueError(f"Unsupported progress stage: {self.stage!r}")
        if not 0.0 <= self.file_progress <= 1.0:
            raise ValueError("file_progress must be in [0.0, 1.0]")
        if self.stage_progress is not None and not 0.0 <= self.stage_progress <= 1.0:
            raise ValueError("stage_progress must be in [0.0, 1.0] or None")
        if self.processed_seconds is not None and self.processed_seconds < 0:
            raise ValueError("processed_seconds must be non-negative")
        if self.total_seconds is not None and self.total_seconds < 0:
            raise ValueError("total_seconds must be non-negative")
        if (
            self.processed_seconds is not None
            and self.total_seconds is not None
            and self.processed_seconds > self.total_seconds
        ):
            raise ValueError("processed_seconds must not exceed total_seconds")


_ALL_STAGES: dict[str, None] = {
    "preparing": None,
    "conversion": None,
    "preprocessing": None,
    "transcription": None,
    "diarization": None,
    "export": None,
    "finalizing": None,
}


class ProgressPlan:
    """Shared progress bands and monotonic normalizer."""

    _PLAN_WITHOUT: dict[str, tuple[float, float]] = {
        "preparing": (0.0, 0.02),
        "conversion": (0.02, 0.12),
        "preprocessing": (0.12, 0.15),
        "transcription": (0.15, 0.95),
        "export": (0.95, 0.99),
        "finalizing": (0.99, 1.0),
    }

    _PLAN_WITH: dict[str, tuple[float, float]] = {
        "preparing": (0.0, 0.02),
        "conversion": (0.02, 0.10),
        "preprocessing": (0.10, 0.12),
        "transcription": (0.12, 0.70),
        "diarization": (0.70, 0.95),
        "export": (0.95, 0.99),
        "finalizing": (0.99, 1.0),
    }

    def __init__(self, *, has_diarization: bool) -> None:
        self._plan = self._PLAN_WITH if has_diarization else self._PLAN_WITHOUT
        self._last_file_progress = 0.0

    def map_stage_to_file_progress(
        self, stage: ProgressStage, stage_progress: float | None
    ) -> float | None:
        """Convert stage-specific progress into full file progress."""

        if stage_progress is None:
            return None
        start, end = self._plan[stage]
        return start + (end - start) * stage_progress

    def normalize_event(self, event: ProgressEvent) -> ProgressEvent:
        """Normalize event stage/file progress and enforce non-decreasing file progress."""

        mapped = self.map_stage_to_file_progress(event.stage, event.stage_progress)
        file_progress = self._last_file_progress if mapped is None else mapped

        if file_progress < self._last_file_progress:
            file_progress = self._last_file_progress
        file_progress = min(max(file_progress, 0.0), 1.0)
        self._last_file_progress = file_progress

        return replace(event, file_progress=file_progress)
