"""Кооперативная отмена обработки посреди файла."""

from __future__ import annotations

from collections.abc import Callable

CancelCheck = Callable[[], bool]


class ProcessingCancelled(RuntimeError):
    """Пользователь отменил обработку; частичные результаты не сохраняются."""

    def __init__(self, message: str = "Обработка отменена пользователем") -> None:
        super().__init__(message)


def raise_if_cancelled(cancel_check: CancelCheck | None) -> None:
    if cancel_check is not None and cancel_check():
        raise ProcessingCancelled()
