"""Unit tests for VAD-driven ASR segmentation."""

import os
import sys
import types
from types import SimpleNamespace

import pytest

from src.core.asr import vad
from src.core.asr.vad import PyannoteVadSegmenter, VadUnavailableError, merge_speech_regions


def test_merge_speech_regions_preserves_vad_outer_boundaries():
    boundaries = merge_speech_regions(
        [(3.11909375, 4.5), (4.7, 9.0), (9.2, 19.65659375)],
        audio_duration=20.581587,
    )

    assert boundaries == [(3.11909375, 19.65659375)]


def test_merge_speech_regions_starts_new_chunk_after_completed_long_region():
    boundaries = merge_speech_regions(
        [(0.0, 10.0), (10.1, 16.0), (16.5, 25.0)],
        audio_duration=25.0,
    )

    assert boundaries == [(0.0, 16.0), (16.5, 25.0)]


def test_merge_speech_regions_splits_region_above_strict_limit():
    boundaries = merge_speech_regions(
        [(0.0, 65.0)],
        audio_duration=65.0,
    )

    assert boundaries == pytest.approx([
        (0.0, 65.0 / 3.0),
        (65.0 / 3.0, 130.0 / 3.0),
        (130.0 / 3.0, 65.0),
    ])


def test_merge_speech_regions_does_not_shrink_for_nested_region():
    assert merge_speech_regions(
        [(0.0, 10.0), (2.0, 4.0)],
        audio_duration=10.0,
    ) == [(0.0, 10.0)]


def test_pyannote_segmenter_returns_merged_speech_boundaries():
    class FakeAnnotation:
        def get_timeline(self):
            return self

        def support(self):
            return [
                SimpleNamespace(start=3.11909375, end=6.0),
                SimpleNamespace(start=6.2, end=19.65659375),
            ]

    class FakePipeline:
        def __call__(self, audio_path):
            assert audio_path == "sample.wav"
            return FakeAnnotation()

    segmenter = PyannoteVadSegmenter(
        token="hf_test",
        device="cpu",
        pipeline_loader=lambda **_kwargs: FakePipeline(),
    )

    assert segmenter.segment_file("sample.wav", audio_duration=20.581587) == [
        (3.11909375, 19.65659375)
    ]


def test_load_pyannote_pipeline_uses_runtime_compatible_token_parameter(monkeypatch):
    calls = []
    model_instance = object()
    monkeypatch.setenv("HF_TOKEN", "original")

    class FakeModel:
        @classmethod
        def from_pretrained(cls, model_id, use_auth_token=None):
            calls.append(("model", model_id, use_auth_token, os.getenv("HF_TOKEN")))
            return model_instance

    class FakePipeline:
        def __init__(self, segmentation):
            calls.append(("pipeline", segmentation))

        def instantiate(self, parameters):
            calls.append(("instantiate", parameters))

        def to(self, device):
            calls.append(("device", str(device)))
            return None

    fake_pyannote = types.ModuleType("pyannote")
    fake_audio = types.ModuleType("pyannote.audio")
    fake_pipelines = types.ModuleType("pyannote.audio.pipelines")
    fake_audio.Model = FakeModel
    fake_pipelines.VoiceActivityDetection = FakePipeline
    fake_pyannote.audio = fake_audio
    monkeypatch.setitem(sys.modules, "pyannote", fake_pyannote)
    monkeypatch.setitem(sys.modules, "pyannote.audio", fake_audio)
    monkeypatch.setitem(sys.modules, "pyannote.audio.pipelines", fake_pipelines)
    monkeypatch.setattr("src.utils.pyannote_patch.apply_pyannote_patch", lambda: calls.append(("patch",)))

    pipeline = vad.load_pyannote_vad_pipeline(token="hf_runtime", device="cpu")

    assert isinstance(pipeline, FakePipeline)
    assert calls == [
        ("patch",),
        ("model", "pyannote/segmentation-3.0", "hf_runtime", "original"),
        ("pipeline", model_instance),
        ("instantiate", {"min_duration_on": 0.0, "min_duration_off": 0.0}),
        ("device", "cpu"),
    ]


def test_pyannote_segmenter_uses_default_pipeline_loader(monkeypatch):
    class FakeAnnotation:
        def get_timeline(self):
            return self

        def support(self):
            return [SimpleNamespace(start=1.0, end=4.0)]

    class FakePipeline:
        def __call__(self, _audio_path):
            return FakeAnnotation()

    calls = []
    monkeypatch.setattr(
        vad,
        "load_pyannote_vad_pipeline",
        lambda **kwargs: calls.append(kwargs) or FakePipeline(),
    )

    segmenter = PyannoteVadSegmenter(token="hf_default", device="cpu")

    assert segmenter.segment_file("sample.wav", audio_duration=5.0) == [(1.0, 4.0)]
    assert calls == [{"token": "hf_default", "device": "cpu"}]


def test_pyannote_segmenter_can_retry_after_loader_failure():
    class FakePipeline:
        def __call__(self, _audio_path):
            raise AssertionError("not used")

    pipeline = FakePipeline()
    calls = []

    def loader(**_kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise OSError("offline")
        return pipeline

    segmenter = PyannoteVadSegmenter(
        token=None,
        device="cpu",
        pipeline_loader=loader,
    )

    with pytest.raises(VadUnavailableError):
        _ = segmenter.pipeline
    assert segmenter.pipeline is pipeline
    assert len(calls) == 2


def test_load_pyannote_pipeline_supports_modern_token_parameter(monkeypatch):
    calls = []

    class FakeModel:
        @classmethod
        def from_pretrained(cls, model_id, token=None):
            calls.append((model_id, token))
            return object()

    class FakePipeline:
        def __init__(self, segmentation):
            self.segmentation = segmentation

        def instantiate(self, _parameters):
            return None

        def to(self, _device):
            return None

    fake_pyannote = types.ModuleType("pyannote")
    fake_audio = types.ModuleType("pyannote.audio")
    fake_pipelines = types.ModuleType("pyannote.audio.pipelines")
    fake_audio.Model = FakeModel
    fake_pipelines.VoiceActivityDetection = FakePipeline
    fake_pyannote.audio = fake_audio
    monkeypatch.setitem(sys.modules, "pyannote", fake_pyannote)
    monkeypatch.setitem(sys.modules, "pyannote.audio", fake_audio)
    monkeypatch.setitem(sys.modules, "pyannote.audio.pipelines", fake_pipelines)
    monkeypatch.setattr("src.utils.pyannote_patch.apply_pyannote_patch", lambda: None)

    vad.load_pyannote_vad_pipeline(token="hf_modern", device="cpu")

    assert calls == [("pyannote/segmentation-3.0", "hf_modern")]
