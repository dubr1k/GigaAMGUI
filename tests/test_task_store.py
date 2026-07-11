"""Характеризующие тесты task_store — фиксируют схему записи задачи api/web 1:1."""
from src.services import task_store

_BASE_KEYS = {
    "task_id", "status", "created_at", "started_at", "completed_at", "progress",
    "stage_progress", "processed_seconds", "total_seconds", "progress_indeterminate",
    "filename", "file_size", "message",
}


def test_new_task_record_base_fields_match_api():
    rec = task_store.new_task_record("t1", "a.mp3", 123)
    assert set(rec) == _BASE_KEYS
    assert rec["task_id"] == "t1"
    assert rec["filename"] == "a.mp3"
    assert rec["file_size"] == 123
    assert rec["status"] == "pending"
    assert rec["progress"] == 0
    assert rec["progress_indeterminate"] is False
    assert rec["started_at"] is None and rec["completed_at"] is None
    assert rec["stage_progress"] is None
    assert rec["processed_seconds"] is None and rec["total_seconds"] is None
    # дефолтное сообщение api.py
    assert rec["message"] == "Задача в очереди на обработку"
    assert isinstance(rec["created_at"], str) and "T" in rec["created_at"]


def test_new_task_record_web_extra_and_message():
    rec = task_store.new_task_record(
        "t2", "b.wav", 5, message="В очереди",
        extra={"stage": "", "output_formats": [], "enable_diarization": False,
               "num_speakers": None, "user": "alice"},
    )
    assert rec["message"] == "В очереди"
    assert rec["user"] == "alice"
    assert rec["stage"] == ""
    assert rec["output_formats"] == []
    assert rec["enable_diarization"] is False
    assert rec["num_speakers"] is None
    # базовые поля тоже присутствуют
    assert _BASE_KEYS.issubset(set(rec))
