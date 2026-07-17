"""PyTorch backend unit tests with model/IO mocks."""

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
import torch

from src.core.asr import pytorch_backend
from src.core.asr.pytorch_backend import PyTorchBackend
from src.core.asr.vad import PyannoteVadSegmenter


def test_select_device_uses_mps_without_saved_runtime(monkeypatch):
    from src.utils import runtime_manager

    monkeypatch.setattr(runtime_manager, "get_selected_variant", lambda: None)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: True)

    assert PyTorchBackend()._select_device() == "mps"


def test_load_uses_project_runtime_manager(monkeypatch):
    calls = {}
    fake_gigaam = SimpleNamespace(
        load_model=lambda revision, **kwargs: calls.update(revision=revision, **kwargs) or object()
    )
    monkeypatch.setitem(sys.modules, "gigaam", fake_gigaam)
    monkeypatch.setattr(PyTorchBackend, "_select_device", lambda self: "cpu")

    backend = PyTorchBackend(model="ai-sage/GigaAM-v3", revision="e2e_rnnt")

    assert backend.load() is True
    assert calls["revision"] == "e2e_rnnt"


def test_bundled_download_root_prefers_local_bundle(tmp_path, monkeypatch):
    meipass = tmp_path / "meipass"
    model_dir = meipass / "models" / "gigaam"
    model_dir.mkdir(parents=True)
    (model_dir / "v3_e2e_rnnt.ckpt").write_bytes(b"0")
    (model_dir / "v3_e2e_rnnt_tokenizer.model").write_bytes(b"0")

    monkeypatch.setattr(__import__("sys"), "_MEIPASS", str(meipass), raising=False)
    backend = PyTorchBackend()
    assert backend._bundled_download_root() == str(model_dir)


def test_transcribe_longform_filters_empty_text_and_limits_chunks(tmp_path, monkeypatch):
    wav_path = tmp_path / "sample.wav"
    sf.write(wav_path, np.zeros(160000, dtype=np.float32), 16000)

    import torchaudio

    def fail_if_called(*args, **kwargs):
        raise AssertionError("torchaudio.load() must not be used")

    monkeypatch.setattr(torchaudio, "load", fail_if_called)

    backend = PyTorchBackend(segmentation_mode="fixed_chunks")
    called = {"chunks": 0}

    def forward(wav, length):
        called["chunks"] += 1
        return wav, length

    backend.model = SimpleNamespace(
        _device="cpu",
        _dtype=torch.float32,
        head=object(),
        forward=forward,
        decoding=SimpleNamespace(decode=lambda head, encoded, length: ["", "hello"]),
    )
    backend.device = "cpu"

    result = backend.transcribe_longform(str(wav_path))
    # 160000 samples at 16000 Hz = 10s, so one chunk should be bounded by the real audio duration.
    assert result == [{"transcription": "hello", "boundaries": (0.0, 10.0)}]
    assert called["chunks"] == 1


def test_transcribe_longform_extracts_text_from_gigaam_structured_decode(tmp_path):
    wav_path = tmp_path / "sample.wav"
    sf.write(wav_path, np.zeros(16000, dtype=np.float32), 16000)

    backend = PyTorchBackend(segmentation_mode="fixed_chunks")
    backend.model = SimpleNamespace(
        _device="cpu",
        _dtype=torch.float32,
        head=object(),
        forward=lambda wav, length: (wav, length),
        decoding=SimpleNamespace(
            decode=lambda head, encoded, length: [
                ("hello", [1, 2, 3], [0, 1, 2])
            ]
        ),
    )
    backend.device = "cpu"

    assert backend.transcribe_longform(str(wav_path)) == [
        {"transcription": "hello", "boundaries": (0.0, 1.0)}
    ]


def test_transcribe_longform_exposes_absolute_word_timestamps(tmp_path, monkeypatch):
    wav_path = tmp_path / "timed.wav"
    sf.write(wav_path, np.zeros(10 * 16000, dtype=np.float32), 16000)
    monkeypatch.setenv("HF_TOKEN", "hf_test")

    class FakeSegmenter:
        def segment_file(self, _audio_path, *, audio_duration):
            assert audio_duration == 10.0
            return [(3.0, 7.0)]

    backend = PyTorchBackend(vad_segmenter_factory=lambda **_kwargs: FakeSegmenter())
    model = SimpleNamespace(
        _device="cpu",
        _dtype=torch.float32,
        head=object(),
        forward=lambda wav, length: (wav, length),
    )
    model._decode = lambda encoded, encoded_len, wav_len, word_timestamps: [
        (
            "Алло, здравствуйте.",
            [
                SimpleNamespace(text="Алло,", start=0.25, end=0.8),
                SimpleNamespace(text="здравствуйте.", start=1.0, end=2.2),
            ],
        )
    ]
    backend.model = model
    backend.device = "cpu"

    assert backend.transcribe_longform(str(wav_path)) == [{
        "transcription": "Алло, здравствуйте.",
        "boundaries": (3.0, 7.0),
        "words": [
            {"text": "Алло,", "start": 3.25, "end": 3.8},
            {"text": "здравствуйте.", "start": 4.0, "end": 5.2},
        ],
    }]


@pytest.mark.parametrize(
    "decoded_words",
    [None, [], [SimpleNamespace(text=" ", start=0.0, end=0.1)]],
)
def test_transcribe_longform_preserves_text_without_word_records(
    tmp_path,
    decoded_words,
):
    wav_path = tmp_path / "no-word-records.wav"
    sf.write(wav_path, np.zeros(16000, dtype=np.float32), 16000)
    model = SimpleNamespace(
        _device="cpu",
        _dtype=torch.float32,
        head=object(),
        forward=lambda wav, length: (wav, length),
    )
    model._decode = lambda *args, **kwargs: [("распознанный текст", decoded_words)]
    backend = PyTorchBackend(segmentation_mode="fixed_chunks")
    backend.model = model
    backend.device = "cpu"

    assert backend.transcribe_longform(str(wav_path)) == [{
        "transcription": "распознанный текст",
        "boundaries": (0.0, 1.0),
    }]


def test_jittered_boundary_word_is_emitted_once(tmp_path, monkeypatch):
    wav_path = tmp_path / "overlap.wav"
    sf.write(wav_path, np.zeros(20 * 16000, dtype=np.float32), 16000)
    chunks = [
        pytorch_backend.AudioChunk(0, 0, 12 * 16000, 0.0, 10.0, False),
        pytorch_backend.AudioChunk(0, 8 * 16000, 20 * 16000, 10.0, 20.0, True),
    ]
    monkeypatch.setattr(pytorch_backend, "plan_audio_chunks", lambda *args, **kwargs: chunks)

    calls = iter([
        [
            SimpleNamespace(text="слева", start=8.5, end=9.5),
            # Первый decoder сдвинул слово чуть вправо от nominal cut.
            SimpleNamespace(text="граница", start=9.7, end=10.5),
        ],
        [
            # Второй decoder сдвинул то же слово чуть влево от cut.
            SimpleNamespace(text="граница", start=1.5, end=2.3),
            SimpleNamespace(text="справа", start=2.5, end=3.5),
        ],
    ])
    model = SimpleNamespace(
        _device="cpu",
        _dtype=torch.float32,
        head=object(),
        forward=lambda wav, length: (wav, length),
    )
    model._decode = lambda *args, **kwargs: [
        ("unused", next(calls))
    ]
    backend = PyTorchBackend(segmentation_mode="overlap_chunks")
    backend.model = model
    backend.device = "cpu"

    segments = backend.transcribe_longform(str(wav_path))

    assert [segment["transcription"] for segment in segments] == [
        "слева граница",
        "справа",
    ]
    assert sum(
        word["text"] == "граница"
        for segment in segments
        for word in segment.get("words", [])
    ) == 1


def test_transcribe_longform_raises_on_unloaded_model():
    backend = PyTorchBackend()
    try:
        backend.transcribe_longform("/tmp/file.wav")
    except RuntimeError as exc:
        assert "Модель не загружена" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_transcribe_longform_reports_chunk_progress(tmp_path, monkeypatch):
    wav_path = tmp_path / "sample.wav"
    # 45 seconds => 3 chunks (20s,20s,5s)
    sf.write(wav_path, np.zeros(45 * 16000, dtype=np.float32), 16000)

    backend = PyTorchBackend(segmentation_mode="fixed_chunks")
    backend.model = SimpleNamespace(
        _device="cpu",
        _dtype=torch.float32,
        head=object(),
        forward=lambda wav, length: (wav, length),
        decoding=SimpleNamespace(
            decode=lambda head, encoded, length: ["hello"]
        ),
    )
    backend.device = "cpu"

    events = []

    segments = backend.transcribe_longform(
        str(wav_path),
        progress_callback=lambda progress, processed, total: events.append((progress, processed, total)),
    )

    assert segments == [
        {"transcription": "hello", "boundaries": (0.0, 20.0)},
        {"transcription": "hello", "boundaries": (20.0, 40.0)},
        {"transcription": "hello", "boundaries": (40.0, 45.0)},
    ]
    assert events == [
        (20.0 / 45.0, 20.0, 45.0),
        (40.0 / 45.0, 40.0, 45.0),
        (1.0, 45.0, 45.0),
    ]


def test_transcribe_longform_uses_vad_speech_boundaries(tmp_path, monkeypatch):
    wav_path = tmp_path / "vad_sample.wav"
    sf.write(wav_path, np.zeros(10 * 16000, dtype=np.float32), 16000)
    monkeypatch.setenv("HF_TOKEN", "hf_test")

    forwarded_samples = []

    class FakeSegmenter:
        def segment_file(self, audio_path, *, audio_duration):
            assert audio_path == str(wav_path)
            assert audio_duration == 10.0
            return [(3.0, 7.0)]

    backend = PyTorchBackend(
        vad_segmenter_factory=lambda **_kwargs: FakeSegmenter(),
    )
    backend.model = SimpleNamespace(
        _device="cpu",
        _dtype=torch.float32,
        head=object(),
        forward=lambda wav, length: forwarded_samples.append(wav.shape[-1]) or (wav, length),
        decoding=SimpleNamespace(decode=lambda head, encoded, length: ["correct"]),
    )
    backend.device = "cpu"

    result = backend.transcribe_longform(str(wav_path))

    assert result == [{"transcription": "correct", "boundaries": (3.0, 7.0)}]
    assert forwarded_samples == [4 * 16000]


def test_vad_long_region_uses_overlap_and_stitches_decoder_text(tmp_path, monkeypatch):
    wav_path = tmp_path / "continuous_speech.wav"
    sf.write(wav_path, np.ones(41 * 16000, dtype=np.float32), 16000)
    monkeypatch.setenv("HF_TOKEN", "hf_test")
    forwarded_samples = []
    decoded = iter([
        "Начало фразы общие слова...",
        "общие слова продолжается без разрыва",
    ])

    class FakeSegmenter:
        def segment_file(self, _audio_path, *, audio_duration):
            assert audio_duration == 41.0
            return [(0.0, 41.0)]

    backend = PyTorchBackend(
        vad_segmenter_factory=lambda **_kwargs: FakeSegmenter(),
    )
    backend.model = SimpleNamespace(
        _device="cpu",
        _dtype=torch.float32,
        head=object(),
        forward=lambda wav, length: forwarded_samples.append(wav.shape[-1]) or (wav, length),
        decoding=SimpleNamespace(decode=lambda *_args: [next(decoded)]),
    )
    backend.device = "cpu"

    assert backend.transcribe_longform(str(wav_path)) == [
        {"transcription": "Начало фразы общие слова", "boundaries": (0.0, 20.5)},
        {"transcription": "продолжается без разрыва", "boundaries": (20.5, 41.0)},
    ]
    assert len(forwarded_samples) == 2
    assert max(forwarded_samples) <= 30 * 16000
    assert sum(forwarded_samples) > 41 * 16000


def test_transcribe_longform_enables_pyannote_vad_by_default(tmp_path, monkeypatch):
    wav_path = tmp_path / "default_vad.wav"
    sf.write(wav_path, np.zeros(10 * 16000, dtype=np.float32), 16000)
    monkeypatch.setenv("HF_TOKEN", "hf_default")
    calls = []

    class FakeSegmenter:
        def __init__(self, *, token, device):
            calls.append((token, device))

        def segment_file(self, _audio_path, *, audio_duration):
            assert audio_duration == 10.0
            return [(2.0, 4.0)]

    monkeypatch.setattr(pytorch_backend, "PyannoteVadSegmenter", FakeSegmenter, raising=False)
    backend = PyTorchBackend()
    backend.model = SimpleNamespace(
        _device="cpu",
        _dtype=torch.float32,
        head=object(),
        forward=lambda wav, length: (wav, length),
        decoding=SimpleNamespace(decode=lambda head, encoded, length: ["vad"]),
    )
    backend.device = "cpu"

    assert backend.transcribe_longform(str(wav_path)) == [
        {"transcription": "vad", "boundaries": (2.0, 4.0)}
    ]
    assert calls == [("hf_default", "cpu")]


def test_transcribe_longform_uses_fixed_chunks_when_explicitly_disabled(tmp_path, monkeypatch):
    wav_path = tmp_path / "fallback.wav"
    sf.write(wav_path, np.zeros(16000, dtype=np.float32), 16000)
    monkeypatch.delenv("HF_TOKEN", raising=False)

    backend = PyTorchBackend(segmentation_mode="fixed_chunks")
    backend.model = SimpleNamespace(
        _device="cpu",
        _dtype=torch.float32,
        head=object(),
        forward=lambda wav, length: (wav, length),
        decoding=SimpleNamespace(decode=lambda head, encoded, length: ["fallback"]),
    )
    backend.device = "cpu"

    assert backend.transcribe_longform(str(wav_path)) == [
        {"transcription": "fallback", "boundaries": (0.0, 1.0)}
    ]
    capabilities = backend.capabilities()
    assert capabilities.segmentation_mode == "fixed_chunks"
    assert "ASR_SEGMENTATION_MODE" in capabilities.segmentation_fallback_reason


def test_transcribe_longform_falls_back_when_vad_is_unavailable(tmp_path, monkeypatch):
    wav_path = tmp_path / "vad_error.wav"
    sf.write(wav_path, np.zeros(16000, dtype=np.float32), 16000)
    monkeypatch.setenv("HF_TOKEN", "hf_denied")

    class FailingSegmenter:
        def segment_file(self, _audio_path, *, audio_duration):
            raise RuntimeError("segmentation access denied for hf_denied")

    backend = PyTorchBackend(
        vad_segmenter_factory=lambda **_kwargs: FailingSegmenter(),
    )
    backend.model = SimpleNamespace(
        _device="cpu",
        _dtype=torch.float32,
        head=object(),
        forward=lambda wav, length: (wav, length),
        decoding=SimpleNamespace(decode=lambda head, encoded, length: ["fallback"]),
    )
    backend.device = "cpu"
    messages = []
    backend._logger = messages.append

    assert backend.transcribe_longform(str(wav_path)) == [
        {"transcription": "fallback", "boundaries": (0.0, 1.0)}
    ]
    capabilities = backend.capabilities()
    assert capabilities.segmentation_mode == "overlap_chunks"
    assert "RuntimeError" in capabilities.segmentation_fallback_reason
    assert "segmentation access denied" not in capabilities.segmentation_fallback_reason
    assert "hf_denied" not in capabilities.segmentation_fallback_reason
    assert messages
    assert all("hf_denied" not in message for message in messages)


def test_vad_failure_uses_overlap_instead_of_hard_20_second_cuts(tmp_path):
    wav_path = tmp_path / "fallback_long.wav"
    sf.write(wav_path, np.ones(41 * 16000, dtype=np.float32), 16000)
    forwarded_samples = []

    class FailingSegmenter:
        def segment_file(self, _audio_path, *, audio_duration):
            raise RuntimeError("offline")

    backend = PyTorchBackend(
        vad_segmenter_factory=lambda **_kwargs: FailingSegmenter(),
    )
    backend.model = SimpleNamespace(
        _device="cpu",
        _dtype=torch.float32,
        head=object(),
        forward=lambda wav, length: forwarded_samples.append(wav.shape[-1]) or (wav, length),
        decoding=SimpleNamespace(decode=lambda *_args: ["уникальный фрагмент"]),
    )
    backend.device = "cpu"

    backend.transcribe_longform(str(wav_path))

    assert backend.capabilities().segmentation_mode == "overlap_chunks"
    assert len(forwarded_samples) == 3
    assert max(forwarded_samples) <= 20 * 16000
    assert sum(forwarded_samples) > 41 * 16000


def test_transcribe_longform_attempts_cached_vad_without_token(tmp_path, monkeypatch):
    wav_path = tmp_path / "cached_vad.wav"
    sf.write(wav_path, np.zeros(16000, dtype=np.float32), 16000)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    factory_calls = []

    class FakeSegmenter:
        def segment_file(self, _audio_path, *, audio_duration):
            return [(0.0, audio_duration)]

    backend = PyTorchBackend(
        vad_segmenter_factory=lambda **kwargs: factory_calls.append(kwargs) or FakeSegmenter(),
    )
    backend.model = SimpleNamespace(
        _device="cpu",
        _dtype=torch.float32,
        head=object(),
        forward=lambda wav, length: (wav, length),
        decoding=SimpleNamespace(decode=lambda head, encoded, length: ["cached"]),
    )
    backend.device = "cpu"

    assert backend.transcribe_longform(str(wav_path))[0]["transcription"] == "cached"
    assert factory_calls == [{"token": None, "device": "cpu"}]


def test_transcribe_longform_caches_initialization_failure_until_token_changes(tmp_path, monkeypatch):
    wav_path = tmp_path / "vad_init_failure.wav"
    sf.write(wav_path, np.zeros(16000, dtype=np.float32), 16000)
    factory_calls = []

    def factory(**kwargs):
        factory_calls.append(kwargs)
        return PyannoteVadSegmenter(
            **kwargs,
            pipeline_loader=lambda **_loader_kwargs: (_ for _ in ()).throw(OSError("offline")),
        )

    backend = PyTorchBackend(vad_segmenter_factory=factory)
    backend.model = SimpleNamespace(
        _device="cpu",
        _dtype=torch.float32,
        head=object(),
        forward=lambda wav, length: (wav, length),
        decoding=SimpleNamespace(decode=lambda *_args: ["fallback"]),
    )
    backend.device = "cpu"

    monkeypatch.setenv("HF_TOKEN", "hf_first")
    backend.transcribe_longform(str(wav_path))
    backend.transcribe_longform(str(wav_path))
    monkeypatch.setenv("HF_TOKEN", "hf_second")
    backend.transcribe_longform(str(wav_path))

    assert [call["token"] for call in factory_calls] == ["hf_first", "hf_second"]


def test_transcribe_longform_preserves_exact_vad_metadata_boundary(tmp_path, monkeypatch):
    wav_path = tmp_path / "exact_boundary.wav"
    sf.write(wav_path, np.zeros(21 * 16000, dtype=np.float32), 16000)
    monkeypatch.setenv("HF_TOKEN", "hf_test")
    expected = (3.11909375, 19.65659375)

    class FakeSegmenter:
        def segment_file(self, _audio_path, *, audio_duration):
            return [expected]

    backend = PyTorchBackend(vad_segmenter_factory=lambda **_kwargs: FakeSegmenter())
    backend.model = SimpleNamespace(
        _device="cpu",
        _dtype=torch.float32,
        head=object(),
        forward=lambda wav, length: (wav, length),
        decoding=SimpleNamespace(decode=lambda head, encoded, length: ["exact"]),
    )
    backend.device = "cpu"

    assert backend.transcribe_longform(str(wav_path)) == [
        {"transcription": "exact", "boundaries": expected}
    ]


def test_transcribe_longform_reuses_loaded_vad_segmenter(tmp_path, monkeypatch):
    wav_path = tmp_path / "reuse_vad.wav"
    sf.write(wav_path, np.zeros(16000, dtype=np.float32), 16000)
    monkeypatch.setenv("HF_TOKEN", "hf_reuse")
    factory_calls = []

    class FakeSegmenter:
        def segment_file(self, _audio_path, *, audio_duration):
            return [(0.0, audio_duration)]

    backend = PyTorchBackend(
        vad_segmenter_factory=lambda **kwargs: factory_calls.append(kwargs) or FakeSegmenter(),
    )
    backend.model = SimpleNamespace(
        _device="cpu",
        _dtype=torch.float32,
        head=object(),
        forward=lambda wav, length: (wav, length),
        decoding=SimpleNamespace(decode=lambda head, encoded, length: ["ok"]),
    )
    backend.device = "cpu"

    backend.transcribe_longform(str(wav_path))
    backend.transcribe_longform(str(wav_path))

    assert factory_calls == [{"token": "hf_reuse", "device": "cpu"}]


def test_transcribe_longform_treats_empty_vad_timeline_as_no_speech(tmp_path, monkeypatch):
    wav_path = tmp_path / "silence.wav"
    sf.write(wav_path, np.zeros(16000, dtype=np.float32), 16000)
    monkeypatch.setenv("HF_TOKEN", "hf_silence")

    class EmptySegmenter:
        def segment_file(self, _audio_path, *, audio_duration):
            return []

    backend = PyTorchBackend(vad_segmenter_factory=lambda **_kwargs: EmptySegmenter())
    backend.model = SimpleNamespace(
        _device="cpu",
        _dtype=torch.float32,
        head=object(),
        forward=lambda *_args: (_ for _ in ()).throw(AssertionError("ASR must not run")),
        decoding=SimpleNamespace(decode=lambda *_args: ["unexpected"]),
    )
    backend.device = "cpu"
    events = []

    assert backend.transcribe_longform(
        str(wav_path),
        progress_callback=lambda *event: events.append(event),
    ) == []
    assert backend.capabilities().segmentation_mode == "vad"
    assert backend.capabilities().segmentation_fallback_reason is None
    assert events == [(1.0, 1.0, 1.0)]


def test_unload_resets_vad_state_and_releases_device_cache(monkeypatch):
    class FakeSegmenter:
        def segment_file(self, _audio_path, *, audio_duration):
            return []

    backend = PyTorchBackend()
    backend.model = object()
    backend.device = "cuda"
    backend._vad_segmenter = FakeSegmenter()
    backend._vad_segmenter_key = (b"fingerprint", "cpu")
    backend.segmentation_mode = "vad"
    backend.segmentation_fallback_reason = "stale"
    cache_calls = []
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "empty_cache", lambda: cache_calls.append(True))

    backend.unload()

    assert backend.model is None
    assert backend._vad_segmenter is None
    assert backend._vad_segmenter_key is None
    assert backend.segmentation_mode == "not_run"
    assert backend.segmentation_fallback_reason is None
    assert cache_calls == [True]


def test_transcribe_longform_serializes_shared_model_inference(tmp_path):
    wav_path = tmp_path / "concurrent.wav"
    sf.write(wav_path, np.zeros(16000, dtype=np.float32), 16000)
    state = {"active": 0, "maximum": 0}
    state_lock = threading.Lock()

    def forward(wav, length):
        with state_lock:
            state["active"] += 1
            state["maximum"] = max(state["maximum"], state["active"])
        time.sleep(0.03)
        with state_lock:
            state["active"] -= 1
        return wav, length

    backend = PyTorchBackend(segmentation_mode="fixed_chunks")
    backend.model = SimpleNamespace(
        _device="cpu",
        _dtype=torch.float32,
        head=object(),
        forward=forward,
        decoding=SimpleNamespace(decode=lambda *_args: ["ok"]),
    )
    backend.device = "cpu"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: backend.transcribe_longform(str(wav_path)), range(2)))

    assert len(results) == 2
    assert state["maximum"] == 1


def test_transcribe_longform_progress_with_short_tail_does_still_complete(tmp_path, monkeypatch):
    # final chunk is smaller than 20ms threshold; callback should still end at 1.0
    wav_path = tmp_path / "sample_tail.wav"
    sf.write(wav_path, np.zeros(321000, dtype=np.float32), 16000)

    backend = PyTorchBackend(segmentation_mode="fixed_chunks")
    backend.model = SimpleNamespace(
        _device="cpu",
        _dtype=torch.float32,
        head=object(),
        forward=lambda wav, length: (wav, length),
        decoding=SimpleNamespace(decode=lambda head, encoded, length: ["tail"]),
    )
    backend.device = "cpu"

    events: list[tuple[float, float, float]] = []
    result = backend.transcribe_longform(
        str(wav_path),
        progress_callback=lambda progress, processed, total: events.append((progress, processed, total)),
    )

    assert result[-1]["boundaries"][1] == 20.0
    assert events[0][1] == 20.0
    assert events[0][2] == 20.0625
    assert events[-1] == (1.0, 20.0625, 20.0625)
