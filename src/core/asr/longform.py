"""Общие части long-form распознавания для ASR backend-ов."""

from __future__ import annotations

import time
from collections.abc import Callable, Hashable, Iterator
from contextlib import contextmanager
from typing import Any

# Неудачную инициализацию VAD не повторяем на каждом файле (батч иначе качал
# бы недоступную модель снова и снова), но и не помним до перезапуска:
# на долгоживущем сервере один сетевой сбой отключал VAD навсегда.
VAD_RETRY_COOLDOWN_SECONDS = 300.0


def _now() -> float:
    return time.monotonic()


class VadFailureMemo:
    """Последний сбой инициализации VAD с ключом окружения и временем."""

    def __init__(self) -> None:
        self.clear()

    def clear(self) -> None:
        self._key: Hashable | None = None
        self._reason: str | None = None
        self._failed_at: float | None = None

    def remember(self, key: Hashable, reason: str) -> None:
        self._key = key
        self._reason = reason
        self._failed_at = _now()

    def blocked(self, key: Hashable) -> str | None:
        """Причина недавнего сбоя для этого ключа или None — можно пробовать."""
        if self._failed_at is None or self._key != key:
            return None
        if _now() - self._failed_at >= VAD_RETRY_COOLDOWN_SECONDS:
            return None
        return self._reason


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
