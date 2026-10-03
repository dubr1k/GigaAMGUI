"""Сшивка перекрывающихся окон держит текст и слова согласованными.

stitch_overlapping_text считает «слова» регуляркой по тексту, а backend-ы
резали этим числом собственный список слов модели и пересобирали из него
текст. «3,5» — одно слово модели, но два слова регулярки; отдельное «—» —
слово модели, но не слово регулярки. Отсюда пропавшее «рублей» на стыке и
задвоенное тире. Срезанное у предыдущего окна «…» оставалось в его words, и
субтитры отбрасывали пословные таймкоды всего сегмента.
"""

from types import SimpleNamespace

import numpy as np
import soundfile as sf
import torch

from src.core.asr import pytorch_backend
from src.core.asr.chunking import AudioChunk, stitch_chunk
from src.core.asr.onnx_backend import OnnxBackend
from src.core.asr.pytorch_backend import PyTorchBackend
from src.core.subtitles import _normalized_words


def _words(*items):
    return [{"text": text, "start": start, "end": end} for text, start, end in items]


def _pytorch_backend(monkeypatch, tmp_path, decoded_windows):
    wav_path = tmp_path / "overlap.wav"
    sf.write(wav_path, np.zeros(20 * 16000, dtype=np.float32), 16000)
    chunks = [
        AudioChunk(0, 0, 12 * 16000, 0.0, 10.0, False),
        AudioChunk(0, 8 * 16000, 20 * 16000, 10.0, 20.0, True),
    ]
    monkeypatch.setattr(pytorch_backend, "plan_audio_chunks", lambda *args, **kwargs: chunks)
    calls = iter(
        [[SimpleNamespace(text=t, start=s, end=e) for t, s, e in window] for window in decoded_windows]
    )
    model = SimpleNamespace(
        _device="cpu",
        _dtype=torch.float32,
        head=object(),
        forward=lambda wav, length: (wav, length),
    )
    model._decode = lambda *args, **kwargs: [("unused", next(calls))]
    backend = PyTorchBackend(segmentation_mode="overlap_chunks")
    backend.model = model
    backend.device = "cpu"
    return backend, str(wav_path)


def _assert_consistent(segments):
    for segment in segments:
        words = segment.get("words")
        if words is not None:
            assert segment["transcription"] == " ".join(word["text"] for word in words)
            assert _normalized_words(segment), segment


def test_decimal_number_in_overlap_does_not_eat_next_word(monkeypatch, tmp_path):
    backend, path = _pytorch_backend(
        monkeypatch,
        tmp_path,
        [
            [("выручка", 7.0, 7.6), ("составила", 8.0, 8.6), ("3,5", 8.7, 9.2), ("миллиона", 9.3, 9.9)],
            [
                ("составила", 0.0, 0.6),
                ("3,5", 0.7, 1.2),
                ("миллиона", 1.3, 1.9),
                ("рублей", 2.5, 3.0),
                ("за", 3.1, 3.3),
                ("год", 3.4, 3.8),
            ],
        ],
    )

    segments = backend.transcribe_longform(path)

    assert [segment["transcription"] for segment in segments] == [
        "выручка составила 3,5 миллиона",
        "рублей за год",
    ]
    _assert_consistent(segments)


def test_standalone_dash_in_overlap_is_not_duplicated(monkeypatch, tmp_path):
    backend, path = _pytorch_backend(
        monkeypatch,
        tmp_path,
        [
            [("и", 7.0, 7.2), ("он", 8.0, 8.3), ("сказал", 8.5, 9.2), ("—", 9.3, 9.4)],
            [("сказал", 0.5, 1.2), ("—", 2.2, 2.3), ("надо", 2.6, 3.0), ("ехать", 3.1, 3.6)],
        ],
    )

    segments = backend.transcribe_longform(path)

    assert [segment["transcription"] for segment in segments] == ["и он сказал —", "надо ехать"]
    _assert_consistent(segments)


def test_stripped_ellipsis_is_removed_from_previous_words_too(monkeypatch, tmp_path):
    backend, path = _pytorch_backend(
        monkeypatch,
        tmp_path,
        [
            [("потом", 7.0, 7.5), ("мы", 8.0, 8.3), ("пошли…", 8.5, 9.4)],
            [("пошли", 1.0, 1.4), ("домой", 2.5, 3.0)],
        ],
    )

    segments = backend.transcribe_longform(path)

    assert [segment["transcription"] for segment in segments] == ["потом мы пошли", "домой"]
    assert segments[0]["words"][-1]["text"] == "пошли"
    _assert_consistent(segments)


def test_onnx_overlap_with_decimal_keeps_following_word(tmp_path):
    wav_path = tmp_path / "onnx.wav"
    sf.write(wav_path, np.zeros(25 * 16000, dtype=np.float32), 16000)

    class Model:
        def __init__(self):
            self.results = [
                SimpleNamespace(
                    text="выручка составила 3,5 миллиона",
                    tokens=[" выручка", " составила", " 3", ",", "5", " миллиона"],
                    timestamps=[0.1, 8.0, 10.0, 10.1, 10.2, 11.0],
                ),
                SimpleNamespace(
                    text="составила 3,5 миллиона рублей",
                    tokens=[" составила", " 3", ",", "5", " миллиона", " рублей"],
                    timestamps=[0.1, 0.5, 0.6, 0.7, 1.0, 10.0],
                ),
            ]

        def with_timestamps(self):
            return self

        def recognize(self, _waveform, *, sample_rate):
            return self.results.pop(0)

    backend = OnnxBackend(
        segmentation_mode="overlap_chunks",
        model_factory=lambda *args, **kwargs: Model(),
        available_provider_probe=lambda: ("CPUExecutionProvider",),
    )
    assert backend.load()

    segments = backend.transcribe_longform(str(wav_path))

    assert [segment["transcription"] for segment in segments][-1].endswith("рублей")
    assert "рублей" in [word["text"] for segment in segments for word in segment.get("words", [])]
    _assert_consistent(segments)


def test_stitch_chunk_trims_model_words_by_text_position():
    stitched = stitch_chunk(
        "выручка составила 3,5 миллиона",
        _words(("выручка", 7.0, 7.6), ("составила", 8.0, 8.6), ("3,5", 8.7, 9.2), ("миллиона", 9.3, 9.9)),
        "составила 3,5 миллиона рублей",
        _words(("составила", 8.0, 8.6), ("3,5", 8.7, 9.2), ("миллиона", 9.3, 9.9), ("рублей", 10.5, 11.0)),
    )

    assert stitched.text == "рублей"
    assert stitched.trim_words == 3


def test_stitch_chunk_without_words_matches_text_stitch():
    stitched = stitch_chunk(
        "Стоимость поездки будет девятьсот восемьдесят рублей...",
        None,
        "девятьсот восемьдесят рублей. Спасибо, всего доброго.",
        None,
    )

    assert stitched.previous_text == "Стоимость поездки будет девятьсот восемьдесят рублей"
    assert stitched.previous_words is None
    assert stitched.text == "Спасибо, всего доброго."
