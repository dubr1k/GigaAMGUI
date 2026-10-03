"""Shared progress event primitives for processing workflows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

ProgressStage = Literal[
    "preparing", "conversion", "preprocessing", "transcription", "diarization", "export", "finalizing"
]


# Колбэк стадии backend-а: (доля 0..1 или None, обработано секунд, всего секунд).
# Единственное определение: asr.types и asr.longform реэкспортируют его.
ProgressCallback = Callable[[float | None, float | None, float | None], None]


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


#: Подписи стадий для клиентов (ru/en), те же, что в PyQt и TUI.
STAGE_LABELS: dict[str, dict[str, str]] = {
    "ru": {
        "preparing": "Подготовка…",
        "conversion": "Конвертация…",
        "preprocessing": "Анализ и подготовка аудио…",
        "transcription": "Распознавание речи…",
        "diarization": "Диаризация…",
        "export": "Экспорт…",
        "finalizing": "Завершение…",
    },
    "en": {
        "preparing": "Preparing…",
        "conversion": "Converting…",
        "preprocessing": "Analyzing and preparing audio…",
        "transcription": "Speech recognition…",
        "diarization": "Speaker diarization…",
        "export": "Exporting…",
        "finalizing": "Finalizing…",
    },
}


def stage_label(stage: str | None, language: str = "ru") -> str:
    """Подпись стадии; неизвестная стадия возвращается как есть."""
    if not stage:
        return ""
    labels = STAGE_LABELS.get(language) or STAGE_LABELS["ru"]
    return labels.get(stage, stage)


@dataclass(frozen=True)
class ProgressSnapshot:
    """Прогресс файла в одной форме, какой бы ни пришёл колбэк процессора."""

    stage: str | None
    stage_progress: float | None
    # Доля всего файла 0..1; None — колбэк её не передал (оставьте прежнюю).
    file_progress: float | None
    processed_seconds: float | None = None
    total_seconds: float | None = None
    message: str | None = None

    @property
    def indeterminate(self) -> bool:
        return self.stage_progress is None

    def label(self, language: str = "ru") -> str:
        return stage_label(self.stage, language)

    def percent(self) -> int | None:
        return None if self.file_progress is None else int(self.file_progress * 100)

    def as_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "stage_progress": self.stage_progress,
            "file_progress": self.file_progress,
            "processed_seconds": self.processed_seconds,
            "total_seconds": self.total_seconds,
            "message": self.message,
        }


def _optional_float(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def coerce_progress(event_or_stage, value=None) -> ProgressSnapshot:
    """Нормализовать аргументы progress-колбэка процессора.

    Принимает ``ProgressEvent`` (основной формат), словарь с теми же полями
    (так событие пересылают worker-ы) или legacy-пару ``(stage, file_progress)``.
    Пять клиентов (web, CLI, TUI-worker, OpenAI-совместимый API, MCP) делали это
    каждый по-своему; ``file_progress`` здесь всегда ограничен 0..1.
    """
    if isinstance(event_or_stage, dict):
        fields = event_or_stage
        get = fields.get
    elif hasattr(event_or_stage, "stage"):
        get = lambda name, default=None: getattr(event_or_stage, name, default)  # noqa: E731
    else:
        stage = None if event_or_stage is None else str(event_or_stage)
        file_progress = _optional_float(value)
        return ProgressSnapshot(
            stage=stage or None,
            stage_progress=None,
            file_progress=None if file_progress is None else min(max(file_progress, 0.0), 1.0),
        )

    stage = get("stage")
    file_progress = _optional_float(get("file_progress"))
    if file_progress is None:
        file_progress = _optional_float(value)
    message = get("message")
    return ProgressSnapshot(
        stage=str(stage) if stage else None,
        stage_progress=_optional_float(get("stage_progress")),
        file_progress=None if file_progress is None else min(max(file_progress, 0.0), 1.0),
        processed_seconds=_optional_float(get("processed_seconds")),
        total_seconds=_optional_float(get("total_seconds")),
        message=str(message) if message is not None else None,
    )


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
