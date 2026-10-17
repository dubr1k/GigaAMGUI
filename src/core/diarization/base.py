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


def backend_import_error(
    exc: ImportError, *, package: str, package_modules: tuple[str, ...], install: str
) -> ImportError:
    """ImportError бэкенда, который называет настоящую причину.

    «Не установлен» верно, только если не нашёлся сам пакет. Если пакет на месте,
    а падает его зависимость (на Windows — обычно DLL), такой текст отправлял
    пользователя переустанавливать то, что уже стоит (issue #72).
    """
    if exc.name in package_modules:
        return ImportError(f"{package} не установлен. Установите: {install}")
    return ImportError(
        f"{package} не загружается — {type(exc).__name__}: {exc}. "
        f"Переустановите зависимости: {install}"
    )
