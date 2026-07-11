"""Единая схема записи задачи транскрибации.

api.py и web_app.py дублировали идентичный набор из 13 базовых полей в своих
_register_task. Здесь он объединён. Persistence/restore намеренно НЕ объединяются:
api — одно-тенантный, web — многопользовательский с index/tombstone; это разные
инварианты, их слияние нарушило бы изоляцию и риск 1:1. web-специфичные поля
(user, stage, output_formats, …) добавляются через параметр extra.
"""
from __future__ import annotations

from datetime import datetime

# Дефолтное сообщение api.py (web передаёт своё через параметр message).
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
