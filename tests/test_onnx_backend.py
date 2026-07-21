from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from src.core.asr import onnx_backend as onnx_backend_module
from src.core.asr.chunking import AudioChunk
from src.core.asr.onnx_backend import OnnxBackend


@pytest.fixture
def use_macos_provider_priority(monkeypatch):
    resolve = onnx_backend_module.resolve_onnx_providers

    def resolve_for_macos(requested, *, available):
        return resolve(requested, available=available, platform_name="darwin")

    monkeypatch.setattr(
        onnx_backend_module,
        "resolve_onnx_providers",
        resolve_for_macos,
    )


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


def test_load_uses_matching_bundled_asr_snapshot_when_path_is_not_explicit(
    tmp_path,
    monkeypatch,
):
    calls = []
    raw_model = _FakeTimestampModel()
    monkeypatch.setattr(
        onnx_backend_module,
        "resolve_model_dir",
        lambda repo_id, **_kwargs: tmp_path / repo_id.replace("/", "--"),
    )
    backend = OnnxBackend(
        model="v3_e2e_rnnt",
        provider="cpu",
        model_factory=lambda *args, **kwargs: calls.append((args, kwargs)) or raw_model,
        available_provider_probe=lambda: ("CPUExecutionProvider",),
    )

    assert backend.load()

    assert calls[0][1]["path"] == tmp_path / "istupakov--gigaam-v3-onnx"


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


def test_overlap_chunks_bound_issue_33_words_to_nominal_timeline(tmp_path, monkeypatch):
    wav_path = tmp_path / "issue-33.wav"
    sf.write(wav_path, np.zeros(20 * 16000, dtype=np.float32), 16000)
    monkeypatch.setattr(
        onnx_backend_module,
        "plan_audio_chunks",
        lambda *args, **kwargs: [
            AudioChunk(0, 0, 12 * 16000, 0.0, 10.0, False),
            AudioChunk(0, 8 * 16000, 20 * 16000, 10.0, 20.0, True),
        ],
    )
    model = _FakeTimestampModel(
        [
            SimpleNamespace(
                text="вопросам в мире были едины",
                tokens=[" вопросам", " в", " мире", " были", " едины"],
                timestamps=[7.0, 8.0, 8.2, 9.0, 9.98],
            ),
            SimpleNamespace(
                text="во всем мире были едины лишнее продолжение",
                tokens=[
                    " во",
                    " всем",
                    " мире",
                    " были",
                    " едины",
                    " лишнее",
                    " продолжение",
                ],
                timestamps=[0.1, 0.4, 0.9, 1.3, 1.7, 1.8, 2.4],
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

    assert [segment["transcription"] for segment in result] == [
        "вопросам в мире были едины",
        "лишнее продолжение",
    ]
    assert [word["text"] for word in result[1]["words"]] == [
        "лишнее",
        "продолжение",
    ]
    for segment in result:
        start, end = segment["boundaries"]
        assert all(
            start <= word["start"] < word["end"] <= end
            for word in segment["words"]
        )
    assert result[0]["words"][-1]["end"] <= result[1]["words"][0]["start"]


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


def test_auto_provider_retries_inference_on_cpu_after_accelerator_failure(
    tmp_path,
    use_macos_provider_priority,
):
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


def test_auto_provider_does_not_retry_user_callback_failure_on_cpu(
    tmp_path,
    use_macos_provider_priority,
):
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


def test_segmentation_mode_defaults_to_configured_value(monkeypatch):
    """ASR_SEGMENTATION_MODE обязан действовать и для ONNX backend."""
    monkeypatch.setattr("src.core.asr.onnx_backend.ASR_SEGMENTATION_MODE", "fixed_chunks")

    backend = OnnxBackend(
        model_factory=lambda *args, **kwargs: _FakeTimestampModel(),
        available_provider_probe=lambda: ("CPUExecutionProvider",),
    )

    assert backend.segmentation_strategy == "fixed_chunks"


def test_stitched_words_stay_consistent_with_transcription(tmp_path):
    """Вставка внутри перекрытия режет из текста больше слов, чем равен overlap.

    stitch_overlapping_text возвращает число *совпавших* слов, а из текста
    удаляет ещё и вставки между совпавшими блоками. Если срезать words по
    overlap и не пересобрать текст, слова и transcription расходятся.
    """
    wav_path = tmp_path / "stitch.wav"
    sf.write(wav_path, np.zeros(25 * 16000, dtype=np.float32), 16000)
    model = _FakeTimestampModel(
        [
            SimpleNamespace(
                text="раз два три четыре",
                tokens=[" раз", " два", " три", " четыре"],
                timestamps=[0.1, 8.0, 9.0, 10.0],
            ),
            SimpleNamespace(
                text="раз два три вставка четыре пять",
                tokens=[" раз", " два", " три", " вставка", " четыре", " пять"],
                timestamps=[0.1, 1.0, 2.0, 3.0, 4.0, 10.0],
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

    assert [item["transcription"] for item in result] == ["раз два три четыре", "пять"]
    for segment in result:
        words = segment.get("words")
        if words is not None:
            assert segment["transcription"] == " ".join(word["text"] for word in words)
    assert [word["text"] for word in result[-1]["words"]] == ["пять"]


def test_failed_vad_initialization_is_not_retried_for_every_file(tmp_path):
    """Недоступную модель VAD нельзя тянуть заново на каждом файле батча."""
    first = tmp_path / "one.wav"
    second = tmp_path / "two.wav"
    for path in (first, second):
        sf.write(path, np.zeros(16000, dtype=np.float32), 16000)

    model = _FakeTimestampModel(
        [
            SimpleNamespace(text="текст", tokens=[" текст"], timestamps=[0.1]),
            SimpleNamespace(text="текст", tokens=[" текст"], timestamps=[0.1]),
        ]
    )
    factory_calls = []

    def failing_factory(**kwargs):
        factory_calls.append(kwargs)
        raise RuntimeError("silero download failed")

    backend = OnnxBackend(
        segmentation_mode="vad",
        model_factory=lambda *args, **kwargs: model,
        available_provider_probe=lambda: ("CPUExecutionProvider",),
        vad_segmenter_factory=failing_factory,
    )
    assert backend.load()

    assert backend.transcribe_longform(str(first))
    assert backend.transcribe_longform(str(second))

    assert len(factory_calls) == 1
    assert backend.capabilities().segmentation_mode == "overlap_chunks"


def test_cpu_retry_reports_explicit_progress_reset(
    tmp_path,
    use_macos_provider_priority,
):
    """Повтор идёт с начала файла, и откат прогресса должен быть объяснён."""
    wav_path = tmp_path / "midway-failure.wav"
    sf.write(wav_path, np.zeros(16000 * 60, dtype=np.float32), 16000)
    logs: list[str] = []

    def _result():
        return SimpleNamespace(text="текст", tokens=[" текст"], timestamps=[0.1])

    class _FailsAfterFirstChunk(_FakeTimestampModel):
        def recognize(self, waveform, *, sample_rate):
            self.recognize_calls.append((np.asarray(waveform).copy(), sample_rate))
            if len(self.recognize_calls) > 1:
                raise RuntimeError("CoreML execution failed")
            return _result()

    accelerator_model = _FailsAfterFirstChunk()
    cpu_model = _FakeTimestampModel([_result() for _ in range(16)])

    def factory(*args, **kwargs):
        first_provider = kwargs["providers"][0]
        if isinstance(first_provider, tuple):
            first_provider = first_provider[0]
        return cpu_model if first_provider == "CPUExecutionProvider" else accelerator_model

    backend = OnnxBackend(
        provider="auto",
        segmentation_mode="fixed_chunks",
        model_factory=factory,
        available_provider_probe=lambda: (
            "CoreMLExecutionProvider",
            "CPUExecutionProvider",
        ),
    )
    assert backend.load(logger=logs.append)

    events: list[tuple[float, float | None, float | None]] = []
    assert backend.transcribe_longform(
        str(wav_path),
        progress_callback=lambda ratio, processed, total: events.append(
            (ratio, processed, total)
        ),
    )

    ratios = [event[0] for event in events]
    reset_at = ratios.index(0.0)
    # До падения прогресс успел вырасти, после сброса снова растёт с нуля.
    assert max(ratios[:reset_at]) > 0.0
    assert events[reset_at] == (0.0, 0.0, 60.0)
    assert ratios[reset_at + 1] > 0.0
    assert any("заново" in message for message in logs)
