"""MLX backend tests with mocked imports."""

import sys
import types

import numpy as np
import pytest

from src.core.asr.chunking import AudioChunk
from src.core.asr.mlx_backend import MLXBackend
from src.core.asr.vad import VadUnavailableError


def test_load_transcribe_unload_is_idempotent_with_mocked_modules(monkeypatch):
    load_calls = {}
    transcribe_calls = {}
    decode_calls = {"count": 0}

    fake_gigaam = types.SimpleNamespace()

    def _load_model(model_type: str, repo_id: str):
        load_calls["model_type"] = model_type
        load_calls["repo_id"] = repo_id
        return "fake-model", "fake-tokenizer"

    fake_audio = types.SimpleNamespace(
        SAMPLE_RATE=16000,
        load_audio=lambda path: np.zeros(32000, dtype=np.float32),
        split_audio=lambda audio: [
            {"start_sample": 0, "end_sample": 16000, "start_sec": 0.0, "end_sec": 1.0},
            {"start_sample": 16000, "end_sample": 32000, "start_sec": 1.0, "end_sec": 2.0},
        ],
        compute_mel=lambda chunk: np.zeros((10, 64), dtype=np.float32),
    )

    def _decode(_encoded, _seq_len):
        values = ["", "hello", "world"]
        value = values[decode_calls["count"] % len(values)]
        decode_calls["count"] += 1
        return value

    fake_gigaam.load_model = _load_model
    fake_gigaam.audio = fake_audio
    fake_gigaam.load_audio = fake_audio.load_audio
    fake_gigaam.compute_mel = fake_audio.compute_mel
    fake_mlx = types.SimpleNamespace(
        array=lambda value: value,
        eval=lambda value: None,
        clear_cache=lambda: transcribe_calls.__setitem__("cache_cleared", True),
    )
    fake_tokenizer = types.SimpleNamespace(decode=lambda text: text.strip())
    backend = MLXBackend(repo="repo/test", segmentation_mode="fixed_chunks")
    monkeypatch.setitem(sys.modules, "gigaam_mlx", fake_gigaam)
    monkeypatch.setitem(sys.modules, "mlx", fake_mlx)
    monkeypatch.setitem(sys.modules, "mlx.core", fake_mlx)

    assert backend.load(lambda message: None) is True
    backend.model = types.SimpleNamespace(
        encode=lambda mel: (mel, mel.shape[1]),
        decode=_decode,
    )
    backend.tokenizer = fake_tokenizer
    assert load_calls == {
        "model_type": "rnnt",
        "repo_id": "repo/test",
    }

    segments = backend.transcribe_longform("/tmp/audio.wav")
    assert segments == [
        {"transcription": "hello", "boundaries": (1.0, 2.0)},
    ]
    assert backend.is_loaded()

    backend.unload()
    assert backend.model is None
    assert backend.tokenizer is None
    assert transcribe_calls.get("cache_cleared") is True


def test_transcribe_longform_reports_chunk_progress_from_chunking_loop(monkeypatch):
    fake_gigaam = types.SimpleNamespace()
    fake_audio = types.SimpleNamespace(
        SAMPLE_RATE=16000,
        load_audio=lambda path: np.zeros(48000, dtype=np.float32),
        split_audio=lambda audio: [
            {"start_sample": 0, "end_sample": 16000, "start_sec": 0.0, "end_sec": 1.0},
            {"start_sample": 16000, "end_sample": 32000, "start_sec": 1.0, "end_sec": 2.0},
            {"start_sample": 32000, "end_sample": 48000, "start_sec": 2.0, "end_sec": 3.0},
        ],
        compute_mel=lambda chunk: np.zeros((10, 64), dtype=np.float32),
    )
    fake_gigaam.audio = fake_audio
    fake_gigaam.load_audio = fake_audio.load_audio
    fake_gigaam.compute_mel = fake_audio.compute_mel
    fake_tokenizer = types.SimpleNamespace(decode=lambda text: text)

    backend = MLXBackend(segmentation_mode="fixed_chunks")
    backend.model = types.SimpleNamespace(
        encode=lambda mel: (mel, mel.shape[1]),
        decode=lambda encoded, seq_len: "text",
    )
    backend.tokenizer = fake_tokenizer
    backend._gigaam_mlx = fake_gigaam

    monkeypatch.setitem(sys.modules, "gigaam_mlx", fake_gigaam)
    monkeypatch.setitem(sys.modules, "mlx.core", types.SimpleNamespace(array=lambda value: value, eval=lambda value: None))
    events = []
    segments = backend._transcribe_in_chunks(
        "/tmp/audio.wav",
        progress_callback=lambda progress_value, processed, total: events.append(
            (progress_value, processed, total)
        ),
    )

    assert segments == [
        {"start": 0.0, "end": 1.0, "text": "text"},
        {"start": 1.0, "end": 2.0, "text": "text"},
        {"start": 2.0, "end": 3.0, "text": "text"},
    ]
    assert events == [
        (1 / 3, 1.0, 3.0),
        (2 / 3, 2.0, 3.0),
        (1.0, 3.0, 3.0),
    ]


def test_transcribe_in_chunks_callback_exception_is_propagated(monkeypatch):
    fake_gigaam = types.SimpleNamespace()
    fake_audio = types.SimpleNamespace(
        SAMPLE_RATE=16000,
        load_audio=lambda path: np.zeros(32000, dtype=np.float32),
        split_audio=lambda audio: [
            {"start_sample": 0, "end_sample": 16000, "start_sec": 0.0, "end_sec": 1.0},
        ],
        compute_mel=lambda chunk: np.zeros((10, 64), dtype=np.float32),
    )
    fake_gigaam.audio = fake_audio
    fake_gigaam.load_audio = fake_audio.load_audio
    fake_gigaam.compute_mel = fake_audio.compute_mel
    backend = MLXBackend(segmentation_mode="fixed_chunks")
    backend.model = types.SimpleNamespace(
        encode=lambda mel: (mel, mel.shape[1]),
        decode=lambda encoded, seq_len: "text",
    )
    backend.tokenizer = types.SimpleNamespace(decode=lambda text: text)
    backend._gigaam_mlx = fake_gigaam

    def raise_from_callback(*_args, **_kwargs):
        raise RuntimeError("callback-cancel")

    monkeypatch.setitem(sys.modules, "gigaam_mlx", fake_gigaam)
    monkeypatch.setitem(sys.modules, "mlx.core", types.SimpleNamespace(array=lambda value: value, eval=lambda value: None))

    with pytest.raises(RuntimeError, match="callback-cancel"):
        backend._transcribe_in_chunks(
            "/tmp/audio.wav",
            progress_callback=raise_from_callback,
        )


def test_gigaam_revision_name_is_normalized_for_upstream_api(monkeypatch):
    load_calls = {}
    fake_gigaam = types.SimpleNamespace(
        load_model=lambda **kwargs: load_calls.update(kwargs) or ("model", "tokenizer")
    )
    monkeypatch.setitem(sys.modules, "gigaam_mlx", fake_gigaam)

    backend = MLXBackend(model="e2e_rnnt", repo="repo/test")

    assert backend.load() is True
    assert load_calls["model_type"] == "rnnt"


def test_transcribe_without_load_raises(monkeypatch):
    backend = MLXBackend()
    with pytest.raises(RuntimeError):
        backend.transcribe_longform("/tmp/audio.wav")


def _ready_backend(
    monkeypatch,
    *,
    duration_seconds: float,
    segmentation_mode: str = "vad",
    vad_segmenter_factory=None,
):
    sample_rate = 16000
    audio = np.zeros(int(duration_seconds * sample_rate), dtype=np.float32)
    mel_lengths = []

    def split_audio(samples):
        chunk_size = 20 * sample_rate
        return [
            {
                "start_sample": start,
                "end_sample": min(start + chunk_size, len(samples)),
                "start_sec": start / sample_rate,
                "end_sec": min(start + chunk_size, len(samples)) / sample_rate,
            }
            for start in range(0, len(samples), chunk_size)
        ]

    def compute_mel(samples):
        mel_lengths.append(len(samples))
        return np.zeros((10, 64), dtype=np.float32)

    fake_audio = types.SimpleNamespace(
        SAMPLE_RATE=sample_rate,
        split_audio=split_audio,
        compute_mel=compute_mel,
    )
    fake_gigaam = types.SimpleNamespace(
        audio=fake_audio,
        load_audio=lambda _path: audio,
    )
    fake_mlx = types.SimpleNamespace(
        array=lambda value: value,
        eval=lambda _value: None,
    )
    monkeypatch.setitem(sys.modules, "mlx.core", fake_mlx)

    backend = MLXBackend(
        segmentation_mode=segmentation_mode,
        vad_segmenter_factory=vad_segmenter_factory,
    )
    backend.model = types.SimpleNamespace(
        encode=lambda mel: (mel, mel.shape[1]),
        decode=lambda _encoded, _seq_len: "decoded",
    )
    backend.tokenizer = types.SimpleNamespace(decode=lambda text: text)
    backend._gigaam_mlx = fake_gigaam
    return backend, mel_lengths


def test_vad_segmentation_preserves_exact_boundaries_for_mlx(monkeypatch):
    expected = (3.11909375, 19.65659375)
    factory_calls = []

    class FakeSegmenter:
        def segment_file(self, audio_path, *, audio_duration):
            assert audio_path == "/tmp/issue-27.wav"
            assert audio_duration == 25.0
            return [expected]

    backend, mel_lengths = _ready_backend(
        monkeypatch,
        duration_seconds=25.0,
        vad_segmenter_factory=lambda **kwargs: factory_calls.append(kwargs) or FakeSegmenter(),
    )
    monkeypatch.setenv("HF_TOKEN", "hf_mlx_vad")
    events = []

    segments = backend.transcribe_longform(
        "/tmp/issue-27.wav",
        progress_callback=lambda *event: events.append(event),
    )

    assert segments == [
        {"transcription": "decoded", "boundaries": expected},
    ]
    assert mel_lengths == [
        int(expected[1] * 16000) - int(expected[0] * 16000),
    ]
    from src.core.asr import mlx_backend

    assert factory_calls == [{
        "token": "hf_mlx_vad",
        "device": mlx_backend.resolve_vad_device(mlx_backend.ASR_VAD_DEVICE),
    }]
    assert backend.capabilities().segmentation_mode == "vad"
    assert backend.capabilities().segmentation_fallback_reason is None
    assert events[-1] == (1.0, 25.0, 25.0)


def test_vad_region_is_resplit_to_mlx_chunk_limit_with_absolute_progress(monkeypatch):
    boundary = (5.0, 31.0)

    class FakeSegmenter:
        def segment_file(self, _audio_path, *, audio_duration):
            assert audio_duration == 45.0
            return [boundary]

    backend, mel_lengths = _ready_backend(
        monkeypatch,
        duration_seconds=45.0,
        vad_segmenter_factory=lambda **_kwargs: FakeSegmenter(),
    )
    events = []

    segments = backend.transcribe_longform(
        "/tmp/long.wav",
        progress_callback=lambda *event: events.append(event),
    )

    assert segments == [
        {"transcription": "decoded", "boundaries": (5.0, 31.0)},
    ]
    assert mel_lengths == [14 * 16000, 14 * 16000]
    assert events == [
        (18 / 45, 18.0, 45.0),
        (31 / 45, 31.0, 45.0),
        (1.0, 45.0, 45.0),
    ]


def test_mlx_overlap_stitches_phrase_instead_of_repeating_boundary(monkeypatch):
    boundary = (5.0, 31.0)

    class FakeSegmenter:
        def segment_file(self, _audio_path, *, audio_duration):
            return [boundary]

    backend, _mel_lengths = _ready_backend(
        monkeypatch,
        duration_seconds=45.0,
        vad_segmenter_factory=lambda **_kwargs: FakeSegmenter(),
    )
    decoded = iter([
        "Начало фразы общие слова...",
        "общие слова продолжается без разрыва",
    ])
    backend.model.decode = lambda _encoded, _seq_len: next(decoded)

    assert backend.transcribe_longform("/tmp/long.wav") == [
        {"transcription": "Начало фразы общие слова", "boundaries": (5.0, 18.0)},
        {"transcription": "продолжается без разрыва", "boundaries": (18.0, 31.0)},
    ]


def test_vad_failure_uses_sanitized_visible_mlx_fallback(monkeypatch):
    class FailingSegmenter:
        def segment_file(self, _audio_path, *, audio_duration):
            raise RuntimeError("segmentation access denied: hf_secret")

    backend, _mel_lengths = _ready_backend(
        monkeypatch,
        duration_seconds=1.0,
        vad_segmenter_factory=lambda **_kwargs: FailingSegmenter(),
    )
    messages = []
    backend._logger = messages.append

    assert backend.transcribe_longform("/tmp/fallback.wav") == [
        {"transcription": "decoded", "boundaries": (0.0, 1.0)},
    ]
    capabilities = backend.capabilities()
    assert capabilities.segmentation_mode == "overlap_chunks"
    assert "RuntimeError" in capabilities.segmentation_fallback_reason
    assert "segmentation access denied" not in capabilities.segmentation_fallback_reason
    assert "hf_secret" not in capabilities.segmentation_fallback_reason
    assert messages
    assert all("hf_secret" not in message for message in messages)


def test_mlx_vad_failure_uses_overlapping_safe_windows(monkeypatch):
    class FailingSegmenter:
        def segment_file(self, _audio_path, *, audio_duration):
            raise RuntimeError("offline")

    backend, mel_lengths = _ready_backend(
        monkeypatch,
        duration_seconds=41.0,
        vad_segmenter_factory=lambda **_kwargs: FailingSegmenter(),
    )

    backend.transcribe_longform("/tmp/fallback-long.wav")

    assert backend.capabilities().segmentation_mode == "overlap_chunks"
    assert len(mel_lengths) == 3
    assert max(mel_lengths) <= 20 * 16000
    assert sum(mel_lengths) > 41 * 16000


def test_empty_vad_timeline_skips_mlx_asr_and_completes_progress(monkeypatch):
    class EmptySegmenter:
        def segment_file(self, _audio_path, *, audio_duration):
            return []

    backend, mel_lengths = _ready_backend(
        monkeypatch,
        duration_seconds=1.0,
        vad_segmenter_factory=lambda **_kwargs: EmptySegmenter(),
    )
    events = []

    assert backend.transcribe_longform(
        "/tmp/silence.wav",
        progress_callback=lambda *event: events.append(event),
    ) == []
    assert mel_lengths == []
    assert backend.capabilities().segmentation_mode == "vad"
    assert events == [(1.0, 1.0, 1.0)]


def test_mlx_vad_segmenter_is_reused_and_unload_resets_state(monkeypatch):
    factory_calls = []

    class FakeSegmenter:
        def segment_file(self, _audio_path, *, audio_duration):
            return [(0.0, audio_duration)]

    backend, _mel_lengths = _ready_backend(
        monkeypatch,
        duration_seconds=1.0,
        vad_segmenter_factory=lambda **kwargs: factory_calls.append(kwargs) or FakeSegmenter(),
    )
    monkeypatch.setenv("HF_TOKEN", "hf_first")

    backend.transcribe_longform("/tmp/reuse.wav")
    backend.transcribe_longform("/tmp/reuse.wav")
    monkeypatch.setenv("HF_TOKEN", "hf_second")
    backend.transcribe_longform("/tmp/reuse.wav")

    assert [call["token"] for call in factory_calls] == ["hf_first", "hf_second"]
    backend.unload()
    assert backend._vad_segmenter is None
    assert backend._vad_segmenter_key is None
    assert backend.capabilities().segmentation_mode == "not_run"


def test_mlx_vad_initialization_failure_is_cached_until_token_changes(monkeypatch):
    factory_calls = []

    class FailingSegmenter:
        def segment_file(self, _audio_path, *, audio_duration):
            raise VadUnavailableError("offline")

    backend, _mel_lengths = _ready_backend(
        monkeypatch,
        duration_seconds=1.0,
        vad_segmenter_factory=lambda **kwargs: factory_calls.append(kwargs) or FailingSegmenter(),
    )

    monkeypatch.setenv("HF_TOKEN", "hf_first")
    backend.transcribe_longform("/tmp/vad-failure.wav")
    backend.transcribe_longform("/tmp/vad-failure.wav")
    monkeypatch.setenv("HF_TOKEN", "hf_second")
    backend.transcribe_longform("/tmp/vad-failure.wav")

    assert [call["token"] for call in factory_calls] == ["hf_first", "hf_second"]


class _ScriptedRnnt:
    """Кадрово-синхронный RNNT: энкодер отдаёт номер кадра, joint его читает.

    Позволяет проверить, что backend забирает из greedy-цикла именно тот кадр,
    на котором был выдан токен, не поднимая настоящую MLX-модель.
    """

    model_type = "rnnt"
    blank_id = 3
    pieces = {0: "▁при", 1: "вет", 2: "▁мир"}

    def __init__(self, emissions, *, seq_len: int = 10):
        per_call = emissions if isinstance(emissions, list) else [emissions]
        self.emissions = [
            {int(frame): list(tokens) for frame, tokens in call.items()} for call in per_call
        ]
        self.seq_len = seq_len
        self._call = -1
        self._frame = -1
        self._pending: list[int] = []
        self.decoder = types.SimpleNamespace(blank_id=self.blank_id, predict=self._predict)
        self.plain_decode_calls = 0

    def encode(self, _mel):
        self._call += 1
        self._frame = -1
        frames = np.arange(self.seq_len, dtype=np.float32).reshape(1, 1, self.seq_len)
        return frames, self.seq_len

    def decode(self, _encoded, _seq_len):
        self.plain_decode_calls += 1
        return []

    def _predict(self, _last_label, state):
        return None, state

    def joint(self, f, _g):
        frame = int(np.asarray(f).reshape(-1)[0])
        if frame != self._frame:
            self._frame = frame
            script = self.emissions[min(self._call, len(self.emissions) - 1)]
            self._pending = list(script.get(frame, []))
        token = self._pending.pop(0) if self._pending else self.blank_id
        logits = np.zeros((1, 1, 1, self.blank_id + 1), dtype=np.float32)
        logits[0, 0, 0, token] = 1.0
        return logits


def _rnnt_backend(monkeypatch, model, *, duration_seconds: float = 1.0):
    sample_rate = 16000
    total_samples = int(duration_seconds * sample_rate)
    fake_audio = types.SimpleNamespace(
        SAMPLE_RATE=sample_rate,
        HOP_LENGTH=160,
        load_audio=lambda path: np.zeros(total_samples, dtype=np.float32),
        split_audio=lambda audio: [
            {
                "start_sample": 0,
                "end_sample": total_samples,
                "start_sec": 0.0,
                "end_sec": duration_seconds,
            }
        ],
        compute_mel=lambda chunk: np.zeros((10, 64), dtype=np.float32),
    )
    fake_gigaam = types.SimpleNamespace(
        audio=fake_audio,
        load_audio=fake_audio.load_audio,
        compute_mel=fake_audio.compute_mel,
    )
    fake_mx = types.SimpleNamespace(
        array=np.array,
        eval=lambda value: None,
        expand_dims=np.expand_dims,
        argmax=np.argmax,
    )
    monkeypatch.setitem(sys.modules, "gigaam_mlx", fake_gigaam)
    monkeypatch.setitem(sys.modules, "mlx.core", fake_mx)

    backend = MLXBackend(segmentation_mode="fixed_chunks")
    backend.model = model
    backend.tokenizer = types.SimpleNamespace(
        decode=lambda ids: "".join(model.pieces[int(i)] for i in ids).replace("▁", " ").strip(),
        id_to_piece=lambda token_id: model.pieces[int(token_id)],
    )
    backend._gigaam_mlx = fake_gigaam
    return backend


def test_rnnt_decoding_attaches_word_timestamps_from_emission_frames(monkeypatch):
    """Кадр эмиссии в greedy-цикле уже известен — из него и берутся времена слов."""
    model = _ScriptedRnnt({0: [0], 2: [1], 5: [2]})
    backend = _rnnt_backend(monkeypatch, model)

    segments = backend.transcribe_longform("/tmp/audio.wav")

    assert len(segments) == 1
    assert segments[0]["transcription"] == "привет мир"
    # 4x subsampling при hop 160 / 16 кГц: один кадр энкодера = 0.04 с.
    assert segments[0]["words"] == [
        {"text": "привет", "start": pytest.approx(0.0), "end": pytest.approx(0.2)},
        {"text": "мир", "start": pytest.approx(0.2), "end": pytest.approx(0.24)},
    ]
    assert model.plain_decode_calls == 0


def test_backend_without_rnnt_head_falls_back_to_plain_decode(monkeypatch):
    """CTC-модель и мок-объекты без joint не обязаны отдавать слова."""
    model = _ScriptedRnnt({0: [0]})
    model.model_type = "ctc"
    backend = _rnnt_backend(monkeypatch, model)

    segments = backend.transcribe_longform("/tmp/audio.wav")

    assert model.plain_decode_calls == 1
    assert all("words" not in segment for segment in segments)


def test_word_timestamps_are_trimmed_by_overlap_stitching(monkeypatch):
    """Сшивка удаляет слова из текста — ровно столько же должно уйти из words."""
    model = _ScriptedRnnt(
        [
            {0: [2], 3: [0], 5: [1]},  # "мир привет"
            {0: [0], 2: [1], 5: [2]},  # "привет мир" — голова повторяет хвост
        ]
    )
    backend = _rnnt_backend(monkeypatch, model, duration_seconds=2.0)
    backend._resolve_chunks = lambda *args, **kwargs: [
        AudioChunk(
            group=0,
            decode_start_sample=0,
            decode_end_sample=16000,
            start_sec=0.0,
            end_sec=1.0,
        ),
        AudioChunk(
            group=0,
            decode_start_sample=8000,
            decode_end_sample=24000,
            start_sec=1.0,
            end_sec=1.5,
            overlaps_previous=True,
        ),
    ]

    segments = backend.transcribe_longform("/tmp/audio.wav")

    assert [segment["transcription"] for segment in segments] == ["мир привет", "мир"]
    # Повторённое "привет" срезано и из текста, и из слов; оставшееся слово
    # отсчитывается от начала окна декодирования (0.5 с), а не от start_sec.
    assert segments[1]["words"] == [
        {"text": "мир", "start": pytest.approx(0.7), "end": pytest.approx(0.74)},
    ]
