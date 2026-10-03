"""Общие части long-form распознавания для ASR backend-ов."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any


@contextmanager
def call_logger(backend: Any, logger: Callable[[str], None] | None) -> Iterator[None]:
    """Направить предупреждения backend-а в журнал текущего вызова.

    ``load()`` запоминает logger, а повторный ``load_model()`` при уже
    загруженной модели возвращается сразу. Без этой подмены предупреждения
    всех следующих файлов (резервное разбиение без VAD и т.п.) уходили в
    журнал задачи, которая когда-то загрузила модель. Вызывается под
    inference-lock backend-а, поэтому параллельные вызовы друг другу не мешают.
    """
    previous = getattr(backend, "_logger", None)
    if logger is not None:
        backend._logger = logger
    try:
        yield
    finally:
        backend._logger = previous
