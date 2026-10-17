import builtins
import os
import sys
import types

import pytest

from src.core.diarization import pyannote_backend
from src.core.model_preparation import (
    ModelPreparationPlan,
    PreparationError,
    PreparationState,
    PreparationStep,
)
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

    # Фабрика создаёт pyannote-менеджер из core.diarization.pyannote_backend.
    monkeypatch.setattr(pyannote_backend, "DiarizationManager", FakeManager)
    monkeypatch.setenv("HF_TOKEN", "hf_first")
    processor = TranscriptionProcessor(object(), _Stats())

    first = processor.diarization_manager
    monkeypatch.setenv("HF_TOKEN", "hf_second")
    second = processor.diarization_manager

    assert first.hf_token == "hf_first"
    assert second.hf_token == "hf_second"
    assert second is not first
    assert created == [first, second]


def test_processor_keeps_injected_onnx_manager_prepared_for_same_provider(monkeypatch):
    # GUI готовит ONNX-диаризацию заранее (build_processing_preparation_plan) и
    # передаёт менеджер в процессор. Прежде _diarization_provider стартовал с
    # None, сравнение None != "auto" выбрасывало готовый менеджер, и обе
    # ONNX-модели грузились второй раз уже внутри обработки файла.
    class PreparedOnnx:
        backend = "onnx"
        provider = "auto"
        hf_token = None

    class Loader:
        requested_provider = "auto"

    def unexpected_factory(*_args, **_kwargs):
        raise AssertionError("готовый менеджер не должен пересоздаваться")

    monkeypatch.setattr(
        "src.core.diarization.factory.create_diarization_backend",
        unexpected_factory,
    )
    prepared = PreparedOnnx()
    processor = TranscriptionProcessor(
        Loader(),
        _Stats(),
        diarization_manager=prepared,
        diarization_backend="onnx",
    )

    assert processor.diarization_manager is prepared


def test_processor_replaces_injected_onnx_manager_after_provider_change(monkeypatch):
    class PreparedOnnx:
        backend = "onnx"
        provider = "auto"
        hf_token = None

    class Loader:
        requested_provider = "cpu"

    created = []
    monkeypatch.setattr(
        "src.core.diarization.factory.create_diarization_backend",
        lambda backend, **kwargs: created.append((backend, kwargs["provider"])) or object(),
    )
    processor = TranscriptionProcessor(
        Loader(),
        _Stats(),
        diarization_manager=PreparedOnnx(),
        diarization_backend="onnx",
    )

    manager = processor.diarization_manager

    assert manager is not None
    assert created == [("onnx", "cpu")]


def test_pipeline_uses_runtime_token_for_all_huggingface_downloads(monkeypatch):
    calls = []
    monkeypatch.setenv("HF_TOKEN", "")

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
    monkeypatch.setenv("HF_TOKEN", "")

    class FakePipeline:
        @classmethod
        def from_pretrained(cls, model_id, use_auth_token=None):
            calls.append(model_id)
            raise TypeError("missing packaged pipeline component")

    _install_pipeline_dependencies(monkeypatch, FakePipeline)
    monkeypatch.setattr(pyannote_backend, "diagnose_hf_access", lambda _token: "all repos OK")
    manager = diarization.DiarizationManager(hf_token="hf_runtime", device="cpu")

    with pytest.raises(ValueError, match="missing packaged pipeline component"):
        manager._load_pipeline()

    assert calls == ["pyannote/speaker-diarization-3.1"]


def test_pyannote_prepare_eagerly_loads_pipeline(monkeypatch):
    pipeline = object()
    events = []
    manager = diarization.DiarizationManager(hf_token="hf_test", device="cpu")
    monkeypatch.setattr(manager, "_load_pipeline", lambda: pipeline)

    prepared = manager.prepare(
        report=lambda state, **kwargs: events.append((state, kwargs)),
        cancel_check=lambda: False,
    )

    assert prepared is manager
    assert manager.pipeline is pipeline
    assert events[-1][0] is PreparationState.LOADING


def test_pipeline_reports_the_dependency_that_breaks_pyannote_import(monkeypatch):
    # Issue #72: pyannote.audio установлен, но его импорт падает на зависимости
    # (на Windows — типично DLL). Любой ImportError превращался в «pyannote.audio
    # не установлен», и пользователь переустанавливал пакет, который стоит.
    _install_pipeline_dependencies(monkeypatch, object)
    _fail_import(
        monkeypatch,
        "pyannote.audio",
        ImportError(
            "DLL load failed while importing _soundfile: Не найден указанный модуль.",
            name="_soundfile",
        ),
    )
    manager = diarization.DiarizationManager(hf_token="hf_runtime", device="cpu")
    plan = ModelPreparationPlan([PreparationStep("diarization", manager.prepare)])

    with pytest.raises(PreparationError) as raised:
        plan.run()

    message = str(raised.value)
    assert "не установлен" not in message
    assert "DLL load failed while importing _soundfile" in message
    assert "_soundfile" in message


def test_pipeline_names_pinned_requirements_when_pyannote_is_missing(monkeypatch):
    # Голый `pip install pyannote.audio` ставит 4.x с torch 2.9+/torchcodec мимо
    # пинов проекта (pyannote.audio==3.1.1, torchaudio<2.9) — подсказка ломала стек.
    _install_pipeline_dependencies(monkeypatch, object)
    _fail_import(
        monkeypatch,
        "pyannote.audio",
        ModuleNotFoundError("No module named 'pyannote.audio'", name="pyannote.audio"),
    )
    manager = diarization.DiarizationManager(hf_token="hf_runtime", device="cpu")

    with pytest.raises(ImportError) as raised:
        manager._load_pipeline()

    message = str(raised.value)
    assert "pyannote.audio не установлен" in message
    assert "requirements.txt" in message
    assert "pip install pyannote.audio" not in message


def _fail_import(monkeypatch, module_name, error):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == module_name:
            raise error
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


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
