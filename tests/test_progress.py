"""Tests for shared processing progress contract and mapping."""

from src.core.progress import ProgressEvent, ProgressPlan


def test_progress_event_rejects_invalid_stage():
    try:
        ProgressEvent(stage="unknown", stage_progress=0.5, file_progress=0.1)  # type: ignore[arg-type]
    except ValueError as exc:
        assert "Unsupported progress stage" in str(exc)
    else:
        raise AssertionError("Expected invalid stage to fail")


def test_progress_event_rejects_invalid_fraction():
    for value in (-0.1, 1.1):
        try:
            ProgressEvent(stage="conversion", stage_progress=value, file_progress=0.5)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected stage_progress={value} to fail")
    for value in (-0.1, 1.1):
        try:
            ProgressEvent(stage="conversion", stage_progress=0.5, file_progress=value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected file_progress={value} to fail")


def test_progress_event_validates_non_decreasing_seconds():
    event = ProgressEvent(
        stage="conversion",
        stage_progress=0.5,
        file_progress=0.1,
        processed_seconds=1.0,
        total_seconds=2.0,
    )
    assert event.processed_seconds == 1.0
    try:
        ProgressEvent(
            stage="conversion",
            stage_progress=0.5,
            file_progress=0.1,
            processed_seconds=3.0,
            total_seconds=2.0,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Expected seconds ordering to fail")


def test_progress_plan_maps_stage_bands_without_diarization():
    plan = ProgressPlan(has_diarization=False)
    assert plan.map_stage_to_file_progress("preparing", 0.0) == 0.0
    assert plan.map_stage_to_file_progress("preparing", 1.0) == 0.02
    assert plan.map_stage_to_file_progress("conversion", 1.0) == 0.12
    assert plan.map_stage_to_file_progress("preprocessing", 1.0) == 0.15
    assert plan.map_stage_to_file_progress("transcription", 0.0) == 0.15
    assert plan.map_stage_to_file_progress("transcription", 1.0) == 0.95


def test_progress_plan_maps_stage_bands_with_diarization():
    plan = ProgressPlan(has_diarization=True)
    assert plan.map_stage_to_file_progress("conversion", 1.0) == 0.10
    assert plan.map_stage_to_file_progress("preprocessing", 1.0) == 0.12
    assert plan.map_stage_to_file_progress("transcription", 1.0) == 0.7
    assert plan.map_stage_to_file_progress("diarization", 1.0) == 0.95


def test_progress_plan_normalizes_to_monotonic_non_decreasing_sequence():
    plan = ProgressPlan(has_diarization=False)
    first = plan.normalize_event(ProgressEvent("preparing", 1.0, 0.02))
    second = plan.normalize_event(ProgressEvent("conversion", 0.9, 0.12))
    regressed = plan.normalize_event(ProgressEvent("conversion", 0.1, 0.05))
    completed = plan.normalize_event(ProgressEvent("transcription", 1.0, 0.95))

    assert first.file_progress == 0.02
    assert second.file_progress > first.file_progress
    assert regressed.file_progress == second.file_progress
    assert completed.file_progress == 0.95


def test_progress_plan_indeterminate_stage_keeps_previous_file_progress():
    plan = ProgressPlan(has_diarization=True)
    with_determined = plan.normalize_event(ProgressEvent("conversion", 0.5, 0.07))
    indeterminate = plan.normalize_event(ProgressEvent("diarization", None, 0.7))
    after = plan.normalize_event(ProgressEvent("diarization", None, 0.7))

    assert with_determined.file_progress > 0.02
    assert indeterminate.file_progress == with_determined.file_progress
    assert after.file_progress == with_determined.file_progress
