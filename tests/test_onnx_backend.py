from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from src.core.asr.onnx_backend import OnnxBackend


class _FakeTimestampModel:
    def __init__(self, results=None):
        self.timestamp_requests = 0
        self.results = list(results or [])
        self.recognize_calls = []

    def with_timestamps(self):
        self.timestamp_requests += 1
        return self

    def recognize(self, waveform, *, sample_rate):
        self.recognize_calls.append((np.asarray(waveform).copy(), sample_rate))
        return self.results.pop(0)


def test_load_passes_model_provider_quantization_and_local_path(tmp_path):
    calls = []
    raw_model = _FakeTimestampModel()
    backend = OnnxBackend(
        model="v3_e2e_rnnt",
        provider="cpu",
        quantization="int8",
        model_dir=str(tmp_path),
        model_factory=lambda *args, **kwargs: calls.append((args, kwargs)) or raw_model,
        available_provider_probe=lambda: ("CPUExecutionProvider",),
    )

    assert backend.load()
    assert calls == [
        (
            ("gigaam-v3-e2e-rnnt",),
            {
                "path": str(tmp_path),
                "quantization": "int8",
                "providers": ["CPUExecutionProvider"],
                "preprocessor_config": {"use_numpy_preprocessors": False},
            },
        )
    ]
    assert raw_model.timestamp_requests == 1
    assert backend.model is raw_model
    assert backend.is_loaded() is True

    capabilities = backend.capabilities()
    assert capabilities.backend == "onnx"
    assert capabilities.model == "v3_e2e_rnnt"
    assert capabilities.device == "cpu"
    assert capabilities.provider == "CPUExecutionProvider"
    assert capabilities.quantization == "int8"


def test_load_failure_is_logged_and_does_not_leave_partial_model():
    messages = []
    backend = OnnxBackend(
        model_factory=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("bad graph")),
        available_provider_probe=lambda: ("CPUExecutionProvider",),
    )

    assert backend.load(logger=messages.append) is False
    assert backend.model is None
    assert backend.is_loaded() is False
    assert any("bad graph" in message for message in messages)


def test_unload_releases_model_and_runtime_metadata():
    backend = OnnxBackend(
        model_factory=lambda *args, **kwargs: _FakeTimestampModel(),
        available_provider_probe=lambda: ("CPUExecutionProvider",),
    )
    assert backend.load()

    backend.unload()

    assert backend.model is None
    assert backend.is_loaded() is False
    assert backend.capabilities().provider is None


def test_bundled_download_root_returns_explicit_model_directory(tmp_path):
    backend = OnnxBackend(model_dir=str(tmp_path))

    assert backend._bundled_download_root() == str(tmp_path)


def test_transcribe_short_file_returns_absolute_words_and_progress(tmp_path):
    wav_path = tmp_path / "short.wav"
    sf.write(wav_path, np.zeros(32000, dtype=np.float32), 16000)
    model = _FakeTimestampModel(
        [
            SimpleNamespace(
                text="первый второй",
                tokens=[" первый", " второй"],
                timestamps=[0.1, 0.8],
            )
        ]
    )
    backend = OnnxBackend(
        segmentation_mode="fixed_chunks",
        model_factory=lambda *args, **kwargs: model,
        available_provider_probe=lambda: ("CPUExecutionProvider",),
    )
    assert backend.load()
    progress = []

    result = backend.transcribe_longform(
        str(wav_path),
        progress_callback=lambda *values: progress.append(values),
    )

    assert result == [
        {
            "transcription": "первый второй",
            "boundaries": (0.0, 2.0),
            "words": [
                {"text": "первый", "start": 0.1, "end": 0.8},
                {"text": "второй", "start": 0.8, "end": 0.84},
            ],
        }
    ]
    assert len(model.recognize_calls) == 1
    assert model.recognize_calls[0][1] == 16000
    assert progress[-1] == (1.0, 2.0, 2.0)
    assert backend.capabilities().segmentation_mode == "fixed_chunks"


def test_overlap_chunks_remove_repeated_text_and_words(tmp_path):
    wav_path = tmp_path / "long.wav"
    sf.write(wav_path, np.zeros(25 * 16000, dtype=np.float32), 16000)
    model = _FakeTimestampModel(
        [
            SimpleNamespace(
                text="первая общая фраза",
                tokens=[" первая", " общая", " фраза"],
                timestamps=[0.1, 10.0, 11.0],
            ),
            SimpleNamespace(
                text="общая фраза финал",
                tokens=[" общая", " фраза", " финал"],
                timestamps=[0.1, 1.0, 10.0],
            ),
        ]
    )
    backend = OnnxBackend(
        segmentation_mode="overlap_chunks",
        model_factory=lambda *args, **kwargs: model,
        available_provider_probe=lambda: ("CPUExecutionProvider",),
    )
    assert backend.load()

    result = backend.transcribe_longform(str(wav_path))

    assert [item["transcription"] for item in result] == [
        "первая общая фраза",
        "финал",
    ]
    assert [word["text"] for item in result for word in item.get("words", [])] == [
        "первая",
        "общая",
        "фраза",
        "финал",
    ]
    assert len(model.recognize_calls) == 2


def test_vad_boundaries_drive_chunk_boundaries(tmp_path):
    wav_path = tmp_path / "vad-long.wav"
    sf.write(wav_path, np.zeros(2 * 16000, dtype=np.float32), 16000)
    model = _FakeTimestampModel(
        [
            SimpleNamespace(
                text="речь",
                tokens=[" речь"],
                timestamps=[0.1],
            )
        ]
    )

    class _Segmenter:
        def segment_file(self, audio_path, *, audio_duration):
            assert audio_path == str(wav_path)
            assert audio_duration == 2.0
            return [(0.25, 1.5)]

    backend = OnnxBackend(
        segmentation_mode="vad",
        model_factory=lambda *args, **kwargs: model,
        available_provider_probe=lambda: ("CPUExecutionProvider",),
        vad_segmenter_factory=lambda **kwargs: _Segmenter(),
    )
    assert backend.load()

    result = backend.transcribe_longform(str(wav_path))

    assert result[0]["boundaries"] == (0.25, 1.5)
    assert backend.capabilities().segmentation_mode == "vad"
    assert backend.capabilities().segmentation_fallback_reason is None


def test_vad_failure_uses_overlap_fallback_with_visible_reason(tmp_path):
    wav_path = tmp_path / "vad-failure.wav"
    sf.write(wav_path, np.zeros(16000, dtype=np.float32), 16000)
    model = _FakeTimestampModel(
        [SimpleNamespace(text="текст", tokens=[" текст"], timestamps=[0.1])]
    )

    class _FailingSegmenter:
        def segment_file(self, audio_path, *, audio_duration):
            raise RuntimeError("vad graph failed")

    backend = OnnxBackend(
        segmentation_mode="vad",
        model_factory=lambda *args, **kwargs: model,
        available_provider_probe=lambda: ("CPUExecutionProvider",),
        vad_segmenter_factory=lambda **kwargs: _FailingSegmenter(),
    )
    assert backend.load()

    assert backend.transcribe_longform(str(wav_path))
    capabilities = backend.capabilities()
    assert capabilities.segmentation_mode == "overlap_chunks"
    assert "vad graph failed" in capabilities.segmentation_fallback_reason


def test_auto_provider_retries_inference_on_cpu_after_accelerator_failure(tmp_path):
    wav_path = tmp_path / "coreml-failure.wav"
    sf.write(wav_path, np.zeros(16000, dtype=np.float32), 16000)
    calls = []

    class _FailingModel(_FakeTimestampModel):
        def recognize(self, waveform, *, sample_rate):
            raise RuntimeError("CoreML execution failed")

    cpu_model = _FakeTimestampModel(
        [SimpleNamespace(text="текст", tokens=[" текст"], timestamps=[0.1])]
    )

    def factory(*args, **kwargs):
        calls.append(kwargs["providers"])
        first_provider = kwargs["providers"][0]
        if isinstance(first_provider, tuple):
            first_provider = first_provider[0]
        if first_provider == "CoreMLExecutionProvider":
            return _FailingModel()
        return cpu_model

    backend = OnnxBackend(
        provider="auto",
        segmentation_mode="fixed_chunks",
        model_factory=factory,
        available_provider_probe=lambda: (
            "CoreMLExecutionProvider",
            "CPUExecutionProvider",
        ),
    )
    assert backend.load()

    assert backend.transcribe_longform(str(wav_path))[0]["transcription"] == "текст"
    assert calls == [
        [
            (
                "CoreMLExecutionProvider",
                {
                    "ModelFormat": "MLProgram",
                    "MLComputeUnits": "ALL",
                    "RequireStaticInputShapes": "1",
                },
            ),
            "CPUExecutionProvider",
        ],
        ["CPUExecutionProvider"],
    ]
    capabilities = backend.capabilities()
    assert capabilities.device == "cpu"
    assert capabilities.provider == "CPUExecutionProvider"
    assert "CoreML execution failed" in capabilities.provider_fallback_reason


def test_explicit_coreml_does_not_silently_retry_on_cpu(tmp_path):
    wav_path = tmp_path / "strict-coreml.wav"
    sf.write(wav_path, np.zeros(16000, dtype=np.float32), 16000)
    calls = []

    class _FailingModel(_FakeTimestampModel):
        def recognize(self, waveform, *, sample_rate):
            raise RuntimeError("CoreML execution failed")

    backend = OnnxBackend(
        provider="coreml",
        segmentation_mode="fixed_chunks",
        model_factory=lambda *args, **kwargs: calls.append(kwargs["providers"])
        or _FailingModel(),
        available_provider_probe=lambda: (
            "CoreMLExecutionProvider",
            "CPUExecutionProvider",
        ),
    )
    assert backend.load()

    with pytest.raises(RuntimeError, match="CoreML execution failed"):
        backend.transcribe_longform(str(wav_path))
    assert calls == [[(
        "CoreMLExecutionProvider",
        {
            "ModelFormat": "MLProgram",
            "MLComputeUnits": "ALL",
            "RequireStaticInputShapes": "1",
        },
    )]]


def test_auto_provider_does_not_retry_user_callback_failure_on_cpu(tmp_path):
    wav_path = tmp_path / "callback-failure.wav"
    sf.write(wav_path, np.zeros(16000, dtype=np.float32), 16000)
    calls = []
    model = _FakeTimestampModel(
        [SimpleNamespace(text="текст", tokens=[" текст"], timestamps=[0.1])]
    )

    def factory(*args, **kwargs):
        calls.append(kwargs["providers"])
        return model

    backend = OnnxBackend(
        provider="auto",
        segmentation_mode="fixed_chunks",
        model_factory=factory,
        available_provider_probe=lambda: (
            "CoreMLExecutionProvider",
            "CPUExecutionProvider",
        ),
    )
    assert backend.load()

    with pytest.raises(RuntimeError, match="consumer failed"):
        backend.transcribe_longform(
            str(wav_path),
            progress_callback=lambda *args: (_ for _ in ()).throw(
                RuntimeError("consumer failed")
            ),
        )

    assert len(calls) == 1
    assert backend.capabilities().provider == "CoreMLExecutionProvider"
