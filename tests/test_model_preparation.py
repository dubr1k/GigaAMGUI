from __future__ import annotations

import threading

import pytest

from src.core.model_preparation import (
    ModelPreparationPlan,
    PreparationCancelled,
    PreparationError,
    PreparationState,
    PreparationStep,
)
from src.services.transcription_service import build_processing_preparation_plan


def test_plan_emits_ordered_component_events_and_returns_results():
    events = []

    def prepare_asr(report, _cancelled):
        report(PreparationState.DOWNLOADING, message="weights", completed_bytes=2, total_bytes=4)
        report(PreparationState.LOADING)
        return "asr-model"

    plan = ModelPreparationPlan([PreparationStep("asr", prepare_asr)])

    results = plan.run(events.append)

    assert results == {"asr": "asr-model"}
    assert [event.state for event in events] == [
        PreparationState.CHECKING,
        PreparationState.DOWNLOADING,
        PreparationState.LOADING,
        PreparationState.READY,
    ]
    assert events[1].component == "asr"
    assert events[1].completed_bytes == 2
    assert events[1].total_bytes == 4


def test_plan_stops_before_next_component_when_cancelled():
    cancelled = threading.Event()
    called = []
    events = []

    def prepare_first(_report, _cancelled):
        called.append("first")
        cancelled.set()

    def prepare_second(_report, _cancelled):
        called.append("second")

    plan = ModelPreparationPlan(
        [
            PreparationStep("first", prepare_first),
            PreparationStep("second", prepare_second),
        ]
    )

    with pytest.raises(PreparationCancelled):
        plan.run(events.append, cancel_check=cancelled.is_set)

    assert called == ["first"]
    assert events[-1].state is PreparationState.CANCELLED
    assert events[-1].component == "first"


def test_plan_wraps_failure_with_component_and_preserves_cause():
    events = []
    original = OSError("network is unavailable")

    def fail(_report, _cancelled):
        raise original

    plan = ModelPreparationPlan([PreparationStep("pyannote", fail)])

    with pytest.raises(PreparationError) as caught:
        plan.run(events.append)

    assert caught.value.component == "pyannote"
    assert caught.value.__cause__ is original
    assert [event.state for event in events] == [
        PreparationState.CHECKING,
        PreparationState.FAILED,
    ]
    assert "network is unavailable" in events[-1].message


def test_component_cancellation_is_reported():
    events = []

    def cancel(_report, _cancelled):
        raise PreparationCancelled("stopped")

    plan = ModelPreparationPlan([PreparationStep("download", cancel)])

    with pytest.raises(PreparationCancelled):
        plan.run(events.append)

    assert [event.state for event in events] == [
        PreparationState.CHECKING,
        PreparationState.CANCELLED,
    ]


def test_successful_component_is_not_prepared_twice():
    calls = 0
    events = []

    def prepare(_report, _cancelled):
        nonlocal calls
        calls += 1
        return object()

    plan = ModelPreparationPlan([PreparationStep("onnx-diarization", prepare)])

    first = plan.run(events.append)
    second = plan.run(events.append)

    assert calls == 1
    assert second["onnx-diarization"] is first["onnx-diarization"]
    assert events[-1].state is PreparationState.READY
    assert events[-1].cached is True


def test_selected_configuration_builds_complete_preparation_plan():
    calls = []

    class Loader:
        requested_provider = "cpu"

        def load_model(self, logger=None):
            calls.append("asr")
            if logger:
                logger("native loader log")
            return True

    class Diarizer:
        backend = "onnx"

        def prepare(self, report=None, cancel_check=None):
            calls.append("diarization")
            report(PreparationState.LOADING, message="segmentation + embeddings")
            return self

    class DeepFilter:
        def is_ready(self):
            return False

        def ensure(self, progress_callback=None, cancel_check=None):
            calls.append("audio-preprocessing")
            progress_callback(2, 4)
            return "/cache/deep-filter"

    diarizer = Diarizer()
    plan = build_processing_preparation_plan(
        Loader(),
        enable_diarization=True,
        diarization_backend="onnx",
        audio_preprocessing_mode="denoise",
        hf_token=None,
        diarization_factory=lambda **kwargs: calls.append(("factory", kwargs)) or diarizer,
        deepfilter_manager_factory=DeepFilter,
    )

    assert plan.components == ("asr", "audio-preprocessing", "diarization")
    prepared = plan.run()

    assert prepared["diarization"] is diarizer
    assert prepared["audio-preprocessing"] == "/cache/deep-filter"
    assert calls[0] == "asr"
    assert calls[-1] == "diarization"


def test_disabled_optional_features_only_prepare_asr():
    class Loader:
        def load_model(self, logger=None):
            return True

    plan = build_processing_preparation_plan(
        Loader(),
        enable_diarization=False,
        diarization_backend="pyannote",
        audio_preprocessing_mode="off",
        diarization_factory=lambda **_kwargs: (_ for _ in ()).throw(AssertionError),
        deepfilter_manager_factory=lambda: (_ for _ in ()).throw(AssertionError),
    )

    assert plan.components == ("asr",)


def test_failed_asr_load_is_a_preparation_error():
    class Loader:
        def load_model(self, logger=None):
            return False

    plan = build_processing_preparation_plan(
        Loader(),
        enable_diarization=False,
        diarization_backend="onnx",
        audio_preprocessing_mode="off",
    )

    with pytest.raises(PreparationError) as caught:
        plan.run()

    assert caught.value.component == "asr"


def test_asr_preparation_reports_each_missing_resource_before_loading():
    events = []

    class Loader:
        requested_provider = "cpu"

        def missing_asr_resources(self):
            return ("GigaAM checkpoint", "GigaAM tokenizer")

        def load_model(self, logger=None):
            logger("loaded")
            return True

    plan = build_processing_preparation_plan(
        Loader(),
        enable_diarization=False,
        diarization_backend="onnx",
        audio_preprocessing_mode="off",
    )

    plan.run(events.append)

    assert [
        event.message
        for event in events
        if event.state is PreparationState.DOWNLOADING
    ] == ["GigaAM checkpoint", "GigaAM tokenizer"]
