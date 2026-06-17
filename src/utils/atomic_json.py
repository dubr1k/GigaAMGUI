"""
Безопасная работа с JSON-файлами: атомарная запись и устойчивая загрузка.

Атомарность достигается записью во временный файл в той же директории
с последующим os.replace() — на POSIX и Windows это атомарная операция,
поэтому падение/kill посреди записи не оставляет битый/усечённый JSON.
"""

import os
import json
import tempfile
from typing import Any


def load_json(path: str, default: Any) -> Any:
    """Загружает JSON; при отсутствии файла или ошибке парсинга возвращает default."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def save_json_atomic(path: str, data: Any):
    """Атомарно сохраняет data как JSON в path."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        # Не оставляем временный файл при любой ошибке
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise
