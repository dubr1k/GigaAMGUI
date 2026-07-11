"""Валидация формата файла и защита имени от path traversal.

Две функции проверки формата сохраняют РАЗНОЕ поведение поверхностей 1:1:
api.py работает по glob-строке SUPPORTED_FORMATS[1], web_app.py — по коллекции
MEDIA_EXTENSIONS. Источник допустимых расширений передаёт вызывающая поверхность.
"""
from __future__ import annotations

from pathlib import Path


def safe_filename(filename: str | None) -> str:
    """Отбрасывает компоненты пути (POSIX и Windows) и NUL; пустое -> 'upload'."""
    name = (filename or "").replace("\\", "/")
    name = name.split("/")[-1]
    name = name.replace("\x00", "").strip()
    return name or "upload"


def is_supported_by_glob(filename: str, glob_exts: str) -> bool:
    """Поведение api.py: glob_exts — строка вида '*.mp3 *.wav'."""
    extensions = glob_exts.split()
    file_ext = Path(filename).suffix.lower()
    return any(file_ext == ext.replace("*", "") for ext in extensions)


def is_supported_by_set(filename: str, extensions) -> bool:
    """Поведение web_app.py: extensions — коллекция суффиксов вида ('.mp3', ...)."""
    file_ext = Path(filename).suffix.lower()
    return file_ext in extensions
