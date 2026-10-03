"""Схема записи фоновой задачи транскрибации: 13 базовых полей.

Записи создаёт веб-панель (`web/task_registry.py`, `WebTaskStore.register`);
её поля (user, stage, output_formats, …) добавляются через параметр `extra`,
хранение, индекс на диске и tombstone-ы — там же. REST API (`api.py`) задач
больше не ведёт: `/v1/audio/transcriptions` отвечает синхронно или потоком SSE.
"""
from __future__ import annotations

from datetime import datetime

# Сообщение по умолчанию; веб-панель передаёт своё («В очереди») через `message`.
DEFAULT_QUEUE_MESSAGE = "Задача в очереди на обработку"


def new_task_record(
    task_id: str,
    filename: str,
    file_size: int,
    *,
    message: str = DEFAULT_QUEUE_MESSAGE,
    extra: dict | None = None,
) -> dict:
    """Создаёт запись задачи с 13 базовыми полями; extra домешивает поверхностные."""
    record = {
        "task_id": task_id,
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "started_at": None,
        "completed_at": None,
        "progress": 0,
        "stage_progress": None,
        "processed_seconds": None,
        "total_seconds": None,
        "progress_indeterminate": False,
        "filename": filename,
        "file_size": file_size,
        "message": message,
    }
    if extra:
        record.update(extra)
    return record
