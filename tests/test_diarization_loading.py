import os
import sys
import types

import pytest

from src.core.processor import TranscriptionProcessor
from src.utils import diarization


class _Stats:
    pass


def test_processor_recreates_manager_when_hf_token_changes(monkeypatch):
    created = []

    class FakeManager:
        def __init__(self, hf_token, device):
            self.hf_token = hf_token
            self.device = device
            created.append(self)

    monkeypatch.setattr(diarization, "DiarizationManager", FakeManager)
    monkeypatch.setenv("HF_TOKEN", "hf_first")
    processor = TranscriptionProcessor(object(), _Stats())

    first = processor.diarization_manager
    monkeypatch.setenv("HF_TOKEN", "hf_second")
    second = processor.diarization_manager

    assert first.hf_token == "hf_first"
    assert second.hf_token == "hf_second"
    assert second is not first
    assert created == [first, second]


def test_pipeline_uses_runtime_token_for_all_huggingface_downloads(monkeypatch):
    calls = []

    class FakePipeline:
        @classmethod
        def from_pretrained(cls, model_id, use_auth_token=None):
            calls.append((model_id, use_auth_token, os.getenv("HF_TOKEN")))
            return cls()

        def to(self, _device):
            return self

    _install_pipeline_dependencies(monkeypatch, FakePipeline)
    manager = diarization.DiarizationManager(hf_token="hf_runtime", device="cpu")

    assert manager._load_pipeline() is not None
    assert calls == [("pyannote/speaker-diarization-3.1", "hf_runtime", "hf_runtime")]


def test_pipeline_preserves_internal_type_error_without_legacy_retry(monkeypatch):
    calls = []

    class FakePipeline:
        @classmethod
        def from_pretrained(cls, model_id, use_auth_token=None):
            calls.append(model_id)
            raise TypeError("missing packaged pipeline component")

    _install_pipeline_dependencies(monkeypatch, FakePipeline)
    monkeypatch.setattr(diarization, "diagnose_hf_access", lambda _token: "all repos OK")
    manager = diarization.DiarizationManager(hf_token="hf_runtime", device="cpu")

    with pytest.raises(ValueError, match="missing packaged pipeline component"):
        manager._load_pipeline()

    assert calls == ["pyannote/speaker-diarization-3.1"]


def _install_pipeline_dependencies(monkeypatch, pipeline_class):
    fake_pyannote = types.ModuleType("pyannote")
    fake_audio = types.ModuleType("pyannote.audio")
    fake_audio.Pipeline = pipeline_class
    fake_pyannote.audio = fake_audio
    monkeypatch.setitem(sys.modules, "pyannote", fake_pyannote)
    monkeypatch.setitem(sys.modules, "pyannote.audio", fake_audio)

    fake_torch = types.ModuleType("torch")
    fake_torch.device = lambda value: value
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(
        "src.utils.pyannote_patch.apply_pyannote_patch",
        lambda: None,
    )
