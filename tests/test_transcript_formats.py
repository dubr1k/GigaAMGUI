"""Pure OpenAI-shaped response builders (no FastAPI)."""
import json

import pytest

from src.services import transcript_formats as tf

UTTS = [
    {"transcription": "Привет,", "boundaries": (0.0, 1.5), "speaker": "SPEAKER_01"},
    {"transcription": "как дела?", "boundaries": (1.5, 3.25), "speaker": "SPEAKER_00",
     "words": [{"text": "как", "start": 1.5, "end": 2.0}, {"text": "дела?", "start": 2.0, "end": 3.25}]},
]


def test_full_text_joins_with_single_spaces():
    assert tf.full_text(UTTS) == "Привет, как дела?"
    assert tf.full_text([]) == ""


def test_usage_rounds_duration_up():
    assert tf.usage(3.25) == {"type": "duration", "seconds": 4}
    assert tf.usage(0.0) == {"type": "duration", "seconds": 0}


def test_build_json():
    assert tf.build_json(UTTS, 3.25) == {"text": "Привет, как дела?", "usage": {"type": "duration", "seconds": 4}}


def test_build_verbose_segments_have_openai_fields():
    out = tf.build_verbose(UTTS, 3.25, "ru", {"segment"}, diarized=False)
    assert out["task"] == "transcribe" and out["language"] == "ru" and out["duration"] == 3.25
    assert out["text"] == "Привет, как дела?"
    seg = out["segments"][1]
    assert seg == {
        "id": 1, "seek": 0, "start": 1.5, "end": 3.25, "text": "как дела?", "tokens": [],
        "temperature": 0.0, "avg_logprob": 0.0, "compression_ratio": 0.0, "no_speech_prob": 0.0,
    }
    assert "words" not in out and "speaker" not in seg


def test_build_verbose_words_and_speakers():
    out = tf.build_verbose(UTTS, 3.25, None, {"segment", "word"}, diarized=True)
    assert out["language"] == "ru"  # default when unknown
    assert out["words"] == [{"word": "как", "start": 1.5, "end": 2.0}, {"word": "дела?", "start": 2.0, "end": 3.25}]
    assert [s["speaker"] for s in out["segments"]] == ["A", "B"]


def test_build_verbose_word_granularity_without_backend_words_gives_empty_list():
    out = tf.build_verbose([{"transcription": "x", "boundaries": (0, 1)}], 1.0, "ru", {"word"}, diarized=False)
    assert out["words"] == []


def test_speaker_letters_by_first_appearance():
    assert tf.speaker_letters(UTTS) == {"SPEAKER_01": "A", "SPEAKER_00": "B"}


def test_build_diarized():
    out = tf.build_diarized(UTTS, 3.25)
    assert out["task"] == "transcribe" and out["duration"] == 3.25
    assert out["segments"][0] == {"id": 0, "type": "transcript.text.segment", "start": 0.0, "end": 1.5, "speaker": "A", "text": "Привет,"}
    assert out["text"] == "Привет, как дела?"


def test_build_diarized_without_speakers_uses_single_speaker_a():
    out = tf.build_diarized([{"transcription": "x", "boundaries": (0, 1)}], 1.0)
    assert out["segments"][0]["speaker"] == "A"


def test_srt_and_vtt_delegate_to_formatters():
    srt = tf.build_srt(UTTS)
    vtt = tf.build_vtt(UTTS)
    assert srt.startswith("1\n00:00:00,000 --> ")
    assert vtt.startswith("WEBVTT")


@pytest.mark.parametrize("fmt,media", [
    ("json", "application/json"), ("verbose_json", "application/json"), ("diarized_json", "application/json"),
    ("text", "text/plain; charset=utf-8"), ("srt", "application/x-subrip"), ("vtt", "text/vtt"),
])
def test_render_media_types(fmt, media):
    body, media_type = tf.render(fmt, UTTS, 3.25, language="ru", granularities={"segment"}, diarized=True, subtitle_options=None)
    assert media_type == media
    if media == "application/json":
        json.dumps(body)  # serialisable
    else:
        assert isinstance(body, str)


def test_render_unknown_format_raises():
    with pytest.raises(ValueError):
        tf.render("xml", UTTS, 1.0, language=None, granularities=set(), diarized=False, subtitle_options=None)
