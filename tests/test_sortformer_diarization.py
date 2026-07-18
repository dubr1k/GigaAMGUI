import inspect
import sys
import types

import pytest

from src.core.processor import TranscriptionProcessor
from src.utils import diarization


class _Stats:
    pass


def test_normalize_diarization_backend_accepts_public_aliases():
    assert diarization.normalize_diarization_backend(None) == "pyannote"
    assert diarization.normalize_diarization_backend("pyannote") == "pyannote"
    assert diarization.normalize_diarization_backend("nvidia") == "sortformer"
    assert diarization.normalize_diarization_backend("sortformer") == "sortformer"
    assert diarization.normalize_diarization_backend("onnx") == "onnx"


def test_normalize_diarization_backend_rejects_unknown_value():
    with pytest.raises(ValueError, match="Неизвестный backend диаризации"):
        diarization.normalize_diarization_backend("mystery")


def test_factory_builds_sortformer_without_hf_token():
    manager = diarization.get_diarization_manager(
        backend="sortformer",
        hf_token=None,
        device="cpu",
    )

    assert isinstance(manager, diarization.SortformerDiarizationManager)
    assert manager.backend == "sortformer"
    assert manager.hf_token is None


def test_processor_keeps_output_formats_positional_compatibility():
    parameters = list(inspect.signature(TranscriptionProcessor.process_file).parameters)

    assert parameters.index("output_formats") < parameters.index("diarization_backend")


def test_sortformer_pipeline_is_shared_process_wide(monkeypatch):
    shared_model = object()
    loads = []
    monkeypatch.setattr(diarization.SortformerDiarizationManager, "_shared_pipelines", {})
    monkeypatch.setattr(
        diarization.SortformerDiarizationManager,
        "_shared_inference_contexts",
        {"cpu": diarization.nullcontext},
    )

    def fake_load(self):
        loads.append(self)
        return shared_model

    monkeypatch.setattr(diarization.SortformerDiarizationManager, "_load_pipeline", fake_load)
    first = diarization.SortformerDiarizationManager(device="cpu")
    second = diarization.SortformerDiarizationManager(device="cpu")

    assert first.pipeline is shared_model
    assert second.pipeline is shared_model
    assert len(loads) == 1


def test_sortformer_loads_v21_with_official_high_latency_configuration(monkeypatch):
    loaded = []

    class Modules:
        chunk_len = None
        chunk_right_context = None
        fifo_len = None
        spkcache_update_period = None
        spkcache_len = None

        def _check_streaming_parameters(self):
            self.checked = True

    class FakeModel:
        def __init__(self):
            self.sortformer_modules = Modules()
            self.device = None
            self.eval_called = False

        @classmethod
        def from_pretrained(cls, model_name):
            loaded.append(model_name)
            return cls()

        def eval(self):
            self.eval_called = True
            return self

        def to(self, device):
            self.device = str(device)
            return self

    fake_models = types.ModuleType("nemo.collections.asr.models")
    fake_models.SortformerEncLabelModel = FakeModel
    fake_asr = types.ModuleType("nemo.collections.asr")
    fake_asr.models = fake_models
    fake_collections = types.ModuleType("nemo.collections")
    fake_collections.asr = fake_asr
    fake_nemo = types.ModuleType("nemo")
    fake_nemo.collections = fake_collections
    fake_torch = types.ModuleType("torch")
    fake_torch.device = lambda value: value
    monkeypatch.setitem(sys.modules, "nemo", fake_nemo)
    monkeypatch.setitem(sys.modules, "nemo.collections", fake_collections)
    monkeypatch.setitem(sys.modules, "nemo.collections.asr", fake_asr)
    monkeypatch.setitem(sys.modules, "nemo.collections.asr.models", fake_models)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    manager = diarization.SortformerDiarizationManager(device="cpu")
    model = manager.pipeline

    assert loaded == ["nvidia/diar_streaming_sortformer_4spk-v2.1"]
    assert model.eval_called is True
    assert model.device == "cpu"
    assert model.sortformer_modules.chunk_len == 340
    assert model.sortformer_modules.chunk_right_context == 40
    assert model.sortformer_modules.fifo_len == 40
    assert model.sortformer_modules.spkcache_update_period == 300
    assert model.sortformer_modules.spkcache_len == 188
    assert model.sortformer_modules.checked is True


def test_sortformer_converts_nemo_segments_and_renames_by_arrival():
    class FakeModel:
        def diarize(self, *, audio, override_config):
            assert audio == ["/tmp/audio.wav"]
            assert override_config.batch_size == 1
            assert override_config.num_workers == 0
            assert override_config.verbose is False
            assert override_config.postprocessing_params.onset == 0.64
            assert override_config.postprocessing_params.offset == 0.74
            assert override_config.postprocessing_params.min_duration_on == 0.1
            assert override_config.postprocessing_params.min_duration_off == 0.15
            return [[
                "1.20 2.50 speaker_1",
                "0.00 1.00 speaker_0",
                {"start": 2.6, "end": 3.0, "speaker": "speaker_1"},
            ]]

    manager = diarization.SortformerDiarizationManager(device="cpu")
    manager._pipeline = FakeModel()
    manager._diarize_config = lambda: types.SimpleNamespace(
        batch_size=1,
        num_workers=0,
        verbose=False,
        postprocessing_params=types.SimpleNamespace(
            onset=0.64,
            offset=0.74,
            min_duration_on=0.1,
            min_duration_off=0.15,
        ),
    )

    segments = manager.diarize("/tmp/audio.wav")

    assert [(item.start, item.end, item.speaker) for item in segments] == [
        (0.0, 1.0, "Спикер №1"),
        (1.2, 2.5, "Спикер №2"),
        (2.6, 3.0, "Спикер №2"),
    ]


def test_sortformer_rejects_malformed_nemo_segment():
    class FakeModel:
        def diarize(self, *, audio, override_config):
            return [["not a valid segment"]]

    manager = diarization.SortformerDiarizationManager(device="cpu")
    manager._pipeline = FakeModel()
    manager._diarize_config = lambda: object()

    with pytest.raises(ValueError, match="Неожиданный сегмент Sortformer"):
        manager.diarize("/tmp/audio.wav")


def test_processor_selects_sortformer_without_hf_token(monkeypatch):
    created = []

    class FakeManager:
        backend = "sortformer"
        hf_token = None

    def fake_factory(*, backend, hf_token, device):
        created.append((backend, hf_token, device))
        return FakeManager()

    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(diarization, "get_diarization_manager", fake_factory)
    processor = TranscriptionProcessor(object(), _Stats())
    processor._active_diarization_backend = "sortformer"

    assert processor.diarization_manager.backend == "sortformer"
    assert created == [("sortformer", None, "auto")]
