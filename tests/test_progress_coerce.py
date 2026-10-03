"""coerce_progress: одна нормализация progress-колбэка для всех клиентов."""

from types import SimpleNamespace

import pytest

from src.core.progress import (
    STAGE_LABELS,
    ProgressEvent,
    ProgressSnapshot,
    coerce_progress,
    stage_label,
)


def test_progress_event_is_taken_field_by_field():
    event = ProgressEvent(
        stage="transcription",
        stage_progress=0.5,
        file_progress=0.4,
        processed_seconds=10.0,
        total_seconds=20.0,
        message="x",
    )

    snapshot = coerce_progress(event)

    assert snapshot == ProgressSnapshot("transcription", 0.5, 0.4, 10.0, 20.0, "x")
    assert snapshot.indeterminate is False
    assert snapshot.percent() == 40
    assert snapshot.label("en") == "Speech recognition…"


def test_forwarded_dict_with_missing_fields():
    snapshot = coerce_progress({"stage": "diarization", "file_progress": 0.8})

    assert snapshot.stage == "diarization"
    assert snapshot.stage_progress is None
    assert snapshot.indeterminate is True
    assert snapshot.file_progress == 0.8
    assert snapshot.processed_seconds is None


def test_legacy_stage_value_pair():
    snapshot = coerce_progress("export", 0.97)

    assert snapshot.stage == "export"
    assert snapshot.file_progress == 0.97
    assert snapshot.stage_progress is None


@pytest.mark.parametrize("raw, expected", [(1.7, 1.0), (-0.2, 0.0), (None, None), ("50%", None)])
def test_file_progress_is_clamped_or_absent(raw, expected):
    assert coerce_progress("conversion", raw).file_progress == expected


def test_object_without_file_progress_falls_back_to_second_argument():
    # Так api.py/mcp_backend обрабатывали событие: доля из события, иначе value.
    event = SimpleNamespace(stage="conversion")

    assert coerce_progress(event, 0.3).file_progress == 0.3


def test_as_dict_matches_the_worker_payload_keys():
    payload = coerce_progress(ProgressEvent(stage="export", stage_progress=1.0, file_progress=0.99)).as_dict()

    assert set(payload) == {
        "stage",
        "stage_progress",
        "file_progress",
        "processed_seconds",
        "total_seconds",
        "message",
    }


def test_stage_labels_cover_every_stage_in_both_languages():
    stages = {"preparing", "conversion", "preprocessing", "transcription", "diarization", "export", "finalizing"}

    assert set(STAGE_LABELS["ru"]) == stages
    assert set(STAGE_LABELS["en"]) == stages
    assert stage_label("conversion") == "Конвертация…"
    assert stage_label("mystery", "en") == "mystery"
    assert stage_label(None) == ""
