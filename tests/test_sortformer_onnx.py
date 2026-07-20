import sys
import types

import numpy as np
import pytest
import soundfile as sf

from src.core.diarization.factory import create_diarization_backend
from src.core.diarization.sortformer_onnx import (
    SORTFORMER_ONNX_FILENAME,
    SortformerOnnxDiarizationManager,
)
from src.core.model_preparation import PreparationState
from src.utils import diarization


class _ModelMeta:
    custom_metadata_map = {
        "chunk_len": "4",
        "fifo_len": "4",
        "spkcache_len": "8",
        "right_context": "1",
    }


class _FakeSession:
    def __init__(self):
        self.calls = []

    def get_modelmeta(self):
        return _ModelMeta()

    def get_inputs(self):
        return [
            types.SimpleNamespace(name=name)
            for name in (
                "chunk",
                "chunk_lengths",
                "spkcache",
                "spkcache_lengths",
                "fifo",
                "fifo_lengths",
            )
        ]

    def get_outputs(self):
        return [
            types.SimpleNamespace(name=name)
            for name in (
                "spkcache_fifo_chunk_preds",
                "chunk_pre_encode_embs",
                "chunk_pre_encode_lengths",
            )
        ]

    def get_providers(self):
        return ["CPUExecutionProvider"]

    def run(self, output_names, inputs):
        self.calls.append((output_names, inputs))
        cache = inputs["spkcache"].shape[1]
        fifo = inputs["fifo"].shape[1]
        valid = (int(inputs["chunk_lengths"][0]) + 7) // 8
        predictions = np.zeros((1, cache + fifo + valid, 4), dtype=np.float32)
        predictions[0, cache + fifo :, 0] = 0.9
        embeddings = np.ones((1, valid, 512), dtype=np.float32)
        lengths = np.asarray([valid], dtype=np.int64)
        return predictions, embeddings, lengths


def test_sortformer_prepare_downloads_missing_artifact_before_loading(tmp_path):
    events = []
    model_path = tmp_path / SORTFORMER_ONNX_FILENAME
    model_path.write_bytes(b"onnx")
    session = _FakeSession()
    downloads = []

    def download():
        downloads.append(True)
        return model_path

    manager = SortformerOnnxDiarizationManager(
        artifact_resolver=lambda: None,
        artifact_downloader=download,
        session_factory=lambda _path, _providers: session,
        checksum=None,
    )

    prepared = manager.prepare(
        report=lambda state, **kwargs: events.append((state, kwargs)),
        cancel_check=lambda: False,
    )

    assert prepared is manager
    assert downloads == [True]
    assert [state for state, _ in events] == [
        PreparationState.DOWNLOADING,
        PreparationState.LOADING,
    ]
    assert manager.session is session


def test_sortformer_prepare_uses_cached_artifact_without_download(tmp_path):
    model_path = tmp_path / SORTFORMER_ONNX_FILENAME
    model_path.write_bytes(b"onnx")
    events = []
    manager = SortformerOnnxDiarizationManager(
        artifact_resolver=lambda: model_path,
        artifact_downloader=lambda: pytest.fail("download must not be called"),
        session_factory=lambda _path, _providers: _FakeSession(),
        checksum=None,
    )

    manager.prepare(report=lambda state, **kwargs: events.append((state, kwargs)))

    assert events[0][0] is PreparationState.LOADING
    assert events[0][1]["cached"] is True
    assert "provider=CPUExecutionProvider" in events[-1][1]["message"]


def test_sortformer_smoke_reports_actual_session_provider_chain():
    manager = SortformerOnnxDiarizationManager(session=_FakeSession(), checksum=None)

    report = manager.smoke_test()

    assert report == {
        "frames": 4,
        "speakers": 4,
        "requested_provider": "auto",
        "session_providers": ["CPUExecutionProvider"],
    }


def test_sortformer_frontend_matches_export_shape_and_is_finite():
    manager = SortformerOnnxDiarizationManager(session=_FakeSession(), checksum=None)
    audio = np.sin(2 * np.pi * 440 * np.arange(16_000, dtype=np.float32) / 16_000)

    features = manager._extract_features(audio)

    assert features.shape == (1, 101, 128)
    assert features.dtype == np.float32
    assert np.isfinite(features).all()


def test_sortformer_streaming_inputs_and_valid_frame_count():
    session = _FakeSession()
    manager = SortformerOnnxDiarizationManager(session=session, checksum=None)
    features = np.zeros((1, 41, 128), dtype=np.float32)

    predictions = manager._process_features(features)

    assert predictions.shape == (6, 4)
    assert len(session.calls) == 2
    first_inputs = session.calls[0][1]
    assert first_inputs["chunk"].shape == (1, 40, 128)
    assert first_inputs["chunk_lengths"].dtype == np.int64
    assert first_inputs["spkcache"].shape == (1, 0, 512)
    assert first_inputs["fifo"].shape == (1, 0, 512)


def test_sortformer_postprocessing_preserves_overlap_and_merges_short_gap():
    manager = SortformerOnnxDiarizationManager(session=_FakeSession(), checksum=None)
    predictions = np.zeros((20, 4), dtype=np.float32)
    predictions[1:8, 0] = 0.9
    predictions[9:15, 0] = 0.9
    predictions[5:12, 1] = 0.9

    segments = manager._predictions_to_segments(predictions, audio_duration=2.0)

    assert [(round(s.start, 2), round(s.end, 2), s.speaker) for s in segments] == [
        (0.02, 1.2, "speaker_0"),
        (0.34, 0.96, "speaker_1"),
    ]


def test_sortformer_diarize_reads_audio_runs_onnx_and_renames_speakers(tmp_path, monkeypatch):
    audio_path = tmp_path / "audio.wav"
    sf.write(audio_path, np.zeros(16_000, dtype=np.float32), 16_000)
    manager = SortformerOnnxDiarizationManager(session=_FakeSession(), checksum=None)
    raw = np.zeros((10, 4), dtype=np.float32)
    raw[1:8, 2] = 0.9
    monkeypatch.setattr(manager, "_process_features", lambda _features: raw)

    segments = manager.diarize(audio_path)

    assert [(segment.speaker, round(segment.start, 2)) for segment in segments] == [
        ("Спикер №1", 0.02),
    ]


def test_factory_routes_sortformer_to_onnx_on_native_windows():
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return "onnx-sortformer"

    result = create_diarization_backend(
        "sortformer",
        provider="directml",
        model_dir="C:/models",
        platform_name="win32",
        sortformer_onnx_factory=factory,
    )

    assert result == "onnx-sortformer"
    assert captured == {"provider": "directml", "model_dir": "C:/models"}


def test_factory_routes_sortformer_to_onnx_when_nemo_is_not_installed():
    captured = {}

    result = create_diarization_backend(
        "sortformer",
        provider="cpu",
        platform_name="linux",
        nemo_available=False,
        sortformer_onnx_factory=lambda **kwargs: captured.update(kwargs) or "onnx",
    )

    assert result == "onnx"
    assert captured == {"provider": "cpu"}


def test_legacy_factory_also_routes_sortformer_to_onnx_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")

    manager = diarization.get_diarization_manager(
        backend="sortformer",
        device="cpu",
        provider="directml",
    )

    assert isinstance(manager, SortformerOnnxDiarizationManager)
    assert manager.provider == "directml"
