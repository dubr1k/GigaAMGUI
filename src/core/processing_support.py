"""Вспомогательные части TranscriptionProcessor без состояния процессора."""

from __future__ import annotations

import inspect
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass


class _TempFiles:
    """Временные файлы одной обработки: личный каталог и удаление на выходе.

    WAV пишутся в системный temp (TMPDIR/TEMP), а не в папку результатов: она
    может не существовать (новая папка в CLI), синхронизироваться облаком или
    лежать на медленном сетевом диске, а недоделанный WAV там остаётся мусором.
    Объём — ~115 МБ на час записи (16 кГц mono), плюс копия после очистки звука.
    Контекст покрывает и конвертацию, и подготовку: исключение между ними
    (например, из progress-колбэка) раньше оставляло WAV на диске.
    """

    def __init__(self, *, protected: str):
        self._protected = os.path.abspath(protected)
        self._paths: list[str] = []
        self.directory = ""

    def __enter__(self) -> _TempFiles:
        self.directory = tempfile.mkdtemp(prefix="gigaam-")
        return self

    def add(self, *paths: str | None) -> None:
        self._paths.extend(path for path in paths if path)

    def __exit__(self, *_exc) -> None:
        # Внешний конвертер может вернуть путь вне нашего каталога, а то и сам
        # исходный файл: удаляем только своё и никогда — исходник.
        for path in self._paths:
            if os.path.abspath(path) == self._protected:
                continue
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass
        shutil.rmtree(self.directory, ignore_errors=True)


class DiarizationSetupError(RuntimeError):
    """Backend диаризации не удалось даже создать (причина — в ``__cause__``)."""


@dataclass
class DiarizationOutcome:
    """Итог стадии диаризации одного файла."""

    utterances: list
    applied: bool = False
    error: str | None = None
    # Диаризацию запросили и она могла запуститься (для pyannote есть токен):
    # тогда об отсутствии файлов _diarize* стоит предупредить.
    attempted: bool = False


def _accepts_event_argument(callback: Callable) -> bool:
    """Можно ли вызвать колбэк одним ProgressEvent (иначе — legacy (stage, value)).

    Решается по сигнатуре, а не пробным вызовом: прежний ``except TypeError``
    принимал TypeError изнутри клиента за несовпадение сигнатуры и повторял
    вызов в legacy-форме, подменяя настоящую ошибку чужой.
    """
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return True
    try:
        signature.bind(None)
    except TypeError:
        return False
    return True


def _accepts_keyword(function: Callable, name: str) -> bool:
    try:
        parameters = inspect.signature(function).parameters
    except (TypeError, ValueError):
        return False
    return name in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )
