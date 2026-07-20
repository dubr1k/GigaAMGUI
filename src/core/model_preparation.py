"""Общий контракт подготовки моделей перед обработкой файлов."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol


class PreparationState(str, Enum):
    CHECKING = "checking"
    DOWNLOADING = "downloading"
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class PreparationEvent:
    component: str
    state: PreparationState
    message: str = ""
    completed_bytes: int | None = None
    total_bytes: int | None = None
    cached: bool = False


class PreparationReporter(Protocol):
    def __call__(
        self,
        state: PreparationState,
        *,
        message: str = "",
        completed_bytes: int | None = None,
        total_bytes: int | None = None,
        cached: bool = False,
    ) -> None: ...


PreparationCallable = Callable[[PreparationReporter, Callable[[], bool]], Any]
PreparationCallback = Callable[[PreparationEvent], None]


@dataclass(frozen=True)
class PreparationStep:
    name: str
    prepare: PreparationCallable


class PreparationCancelled(RuntimeError):
    """Пользователь отменил подготовку до обработки первого файла."""


class PreparationError(RuntimeError):
    """Ошибка подготовки с именем компонента, который её вызвал."""

    def __init__(self, component: str, message: str):
        self.component = component
        super().__init__(f"{component}: {message}")


class ModelPreparationPlan:
    """Последовательно и ровно один раз готовит именованные компоненты."""

    def __init__(self, steps: Iterable[PreparationStep]):
        self._steps = tuple(steps)
        names = [step.name for step in self._steps]
        if len(names) != len(set(names)):
            raise ValueError("Имена компонентов подготовки должны быть уникальными")
        self._results: dict[str, Any] = {}
        self._lock = threading.Lock()

    @property
    def results(self) -> Mapping[str, Any]:
        return dict(self._results)

    @property
    def components(self) -> tuple[str, ...]:
        return tuple(step.name for step in self._steps)

    @staticmethod
    def _cancelled(cancel_check: Callable[[], bool] | None) -> bool:
        return bool(cancel_check and cancel_check())

    def run(
        self,
        callback: PreparationCallback | None = None,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        emit = callback or (lambda _event: None)

        with self._lock:
            for step in self._steps:
                if self._cancelled(cancel_check):
                    raise PreparationCancelled("Подготовка моделей отменена")

                if step.name in self._results:
                    emit(
                        PreparationEvent(
                            component=step.name,
                            state=PreparationState.READY,
                            cached=True,
                        )
                    )
                    continue

                emit(PreparationEvent(step.name, PreparationState.CHECKING))

                def report(
                    state: PreparationState,
                    *,
                    message: str = "",
                    completed_bytes: int | None = None,
                    total_bytes: int | None = None,
                    cached: bool = False,
                    _component_name: str = step.name,
                ) -> None:
                    emit(
                        PreparationEvent(
                            component=_component_name,
                            state=state,
                            message=message,
                            completed_bytes=completed_bytes,
                            total_bytes=total_bytes,
                            cached=cached,
                        )
                    )

                try:
                    result = step.prepare(report, lambda: self._cancelled(cancel_check))
                    if self._cancelled(cancel_check):
                        raise PreparationCancelled("Подготовка моделей отменена")
                except PreparationCancelled:
                    report(PreparationState.CANCELLED)
                    raise
                except Exception as exc:
                    report(PreparationState.FAILED, message=str(exc))
                    raise PreparationError(step.name, str(exc)) from exc

                self._results[step.name] = result
                report(PreparationState.READY)

        return dict(self._results)
