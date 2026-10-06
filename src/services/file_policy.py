"""Валидация формата файла и защита имени от path traversal.

`is_supported_media` — единая проверка медиа-расширения для REST API, веб-панели
и MCP. Раньше api.py сверял суффикс с glob-строкой SUPPORTED_FORMATS[1], а
web_app.py — с кортежем MEDIA_EXTENSIONS; списки совпадают (это проверяет
тест), так что поведение одно. `is_supported_by_glob`/`is_supported_by_set`
оставлены для вызывающих со своим набором расширений (TUI).
"""
from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

from src.config import MEDIA_EXTENSIONS


def safe_filename(filename: str | None) -> str:
    """Отбрасывает компоненты пути (POSIX и Windows) и NUL; пустое -> 'upload'."""
    name = (filename or "").replace("\\", "/")
    name = name.split("/")[-1]
    name = name.replace("\x00", "").strip()
    return name or "upload"


def is_supported_by_set(filename: str, extensions: Collection[str]) -> bool:
    """extensions — коллекция суффиксов вида ('.mp3', ...); регистр имени не важен."""
    return Path(filename).suffix.lower() in extensions


def is_supported_by_glob(filename: str, glob_exts: str) -> bool:
    """glob_exts — строка вида '*.mp3 *.wav'."""
    return is_supported_by_set(filename, {ext.replace("*", "") for ext in glob_exts.split()})


def is_supported_media(filename: str | None) -> bool:
    """Поддерживаемый медиафайл (MEDIA_EXTENSIONS) — для всех серверных поверхностей."""
    return is_supported_by_set(filename or "", MEDIA_EXTENSIONS)
