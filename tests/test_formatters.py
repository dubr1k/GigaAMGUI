"""Характеризующие тесты форматтеров SRT/VTT/Markdown."""
import re

from src.core import formatters
from src.core.subtitles import SRT_SPEAKER_SEPARATOR, SubtitleOptions


class _TF:
    """Минимальный time_formatter: mm:ss."""

    def format_timestamp(self, seconds):
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m:02d}:{s:02d}"


UTTS = [
    {"transcription": "привет мир", "boundaries": (0.0, 1.5), "speaker": None},
    {"transcription": "  ", "boundaries": (1.5, 2.0), "speaker": None},  # пустой -> пропуск
    {"transcription": "второй", "boundaries": (65.25, 70.0), "speaker": "SPEAKER_00"},
    {"transcription": "третий", "boundaries": (70.0, 72.0), "speaker": "SPEAKER_00"},
]


def test_format_timestamp_srt_and_vtt_separators():
    assert formatters.format_timestamp(3661.123, ",") == "01:01:01,123"
    assert formatters.format_timestamp(3661.123, ".") == "01:01:01.123"
    assert formatters.format_timestamp(0.0, ",") == "00:00:00,000"


def test_generate_srt():
    srt = formatters.generate_srt(UTTS)
    assert srt == (
        "1\n"
        "00:00:00,000 --> 00:00:01,500\n"
        "привет мир\n"
        "\n"
        "2\n"
        "00:01:05,250 --> 00:01:12,000\n"
        "SPEAKER_00: второй третий\n"
    )


def test_generate_vtt():
    vtt = formatters.generate_vtt(UTTS)
    assert vtt.startswith("WEBVTT\n\n")
    assert "00:00:00.000 --> 00:00:01.500" in vtt
    assert "<v SPEAKER_00>второй" in vtt
    # пустой сегмент пропущен
    assert vtt.count("-->") == 2


def test_srt_and_vtt_share_phrase_cues_and_line_wrapping():
    utterances = [{
        "transcription": "Первая короткая фраза. Вторая короткая фраза.",
        "boundaries": (1.0, 5.0),
        "speaker": "Спикер №1",
        "words": [
            {"text": "Первая", "start": 1.0, "end": 1.4},
            {"text": "короткая", "start": 1.5, "end": 1.9},
            {"text": "фраза.", "start": 2.0, "end": 2.4},
            {"text": "Вторая", "start": 3.0, "end": 3.4},
            {"text": "короткая", "start": 3.5, "end": 3.9},
            {"text": "фраза.", "start": 4.0, "end": 4.4},
        ],
    }]
    options = SubtitleOptions(max_line_count=2, max_line_width=20)

    srt = formatters.generate_srt(utterances, options)
    vtt = formatters.generate_vtt(utterances, options)

    assert srt.count("-->") == 2
    assert vtt.count("-->") == 2
    assert "00:00:01,000 --> 00:00:02,400" in srt
    assert "00:00:03.000 --> 00:00:04.400" in vtt
    assert "№1: Первая короткая\nфраза." in srt
    assert "<v Спикер №1>Первая короткая\nфраза." in vtt


def test_diarized_subtitle_prefix_respects_max_line_width():
    utterances = [
        {
            "transcription": "Коротко.",
            "boundaries": (0.0, 1.0),
            "speaker": "Спикер №1",
            "words": [{"text": "Коротко.", "start": 0.0, "end": 1.0}],
        },
        {
            "transcription": "12345678901234567890",
            "boundaries": (1.0, 2.0),
            "speaker": "Спикер №1",
            "words": [{
                "text": "12345678901234567890",
                "start": 1.0,
                "end": 2.0,
            }],
        },
    ]
    options = SubtitleOptions(max_line_count=2, max_line_width=20)

    srt = formatters.generate_srt(utterances, options)
    vtt = formatters.generate_vtt(utterances, options)

    srt_payload = [
        line for line in srt.splitlines()
        if line and not line.isdigit() and "-->" not in line
    ]
    # Разметка <v ...> невидима в плеере, поэтому меряем текст без неё.
    vtt_payload = [
        re.sub(r"^<v [^>]+>", "", line)
        for line in vtt.splitlines()
        if line and line != "WEBVTT" and "-->" not in line
    ]
    assert all(len(line) <= 20 for line in [*srt_payload, *vtt_payload])
    assert srt_payload[0].startswith(f"№1{SRT_SPEAKER_SEPARATOR}")


def test_generate_markdown_speaker_header_once():
    md = formatters.generate_markdown(UTTS, "audio.mp3", _TF())
    assert md.startswith("# Транскрипция: audio.mp3")
    assert "*Создано с помощью GigaAM v3 Transcriber*" in md
    # заголовок спикера появляется один раз для двух подряд реплик одного спикера
    assert md.count("### SPEAKER_00") == 1
    assert "`01:05 - 01:10`" in md


def test_srt_labels_speaker_once_while_vtt_attributes_every_cue():
    utterances = [{
        "transcription": "Первая фраза. Вторая фраза.",
        "boundaries": (1.0, 3.0),
        "speaker": "Спикер №1",
        "words": [
            {"text": "Первая", "start": 1.0, "end": 1.4},
            {"text": "фраза.", "start": 1.5, "end": 1.9},
            {"text": "Вторая", "start": 2.0, "end": 2.4},
            {"text": "фраза.", "start": 2.5, "end": 2.9},
        ],
    }]

    srt = formatters.generate_srt(utterances)
    vtt = formatters.generate_vtt(utterances)

    srt_blocks = [block for block in srt.split("\n\n") if block.strip()]
    assert len(srt_blocks) == 2
    assert srt_blocks[0].endswith("Спикер №1: Первая фраза.")
    assert "Спикер" not in srt_blocks[1]
    assert "<" not in srt
    assert vtt.count("<v Спикер №1>") == 2
    assert "</v>" not in vtt
    # Оба формата описывают одни и те же cues с одинаковыми границами.
    srt_times = [
        line.replace(",", ".") for line in srt.splitlines() if "-->" in line
    ]
    vtt_times = [line for line in vtt.splitlines() if "-->" in line]
    assert srt_times == vtt_times


def test_truncated_speaker_label_does_not_start_with_space_in_srt():
    # Регрессия: value[-max_length:] в _fit_speaker может срезать хвост так,
    # что первым символом окажется пробел ("Спикер А"[-2:] == " А"). Строка
    # субтитра не должна начинаться с пробела.
    utterances = [
        {
            "transcription": "Реплика.",
            "boundaries": (0.0, 1.0),
            "speaker": "Спикер А",
            "words": [{"text": "Реплика.", "start": 0.0, "end": 1.0}],
        }
    ]
    options = SubtitleOptions(max_line_width=20)

    srt = formatters.generate_srt(utterances, options)

    srt_payload = [
        line for line in srt.splitlines()
        if line and not line.isdigit() and "-->" not in line
    ]
    assert srt_payload[0] == srt_payload[0].lstrip()


def _utt(text, start, end):
    return {"transcription": text, "boundaries": (start, end), "speaker": None}


def _sentence(n):
    """Предложение ~100 символов с уникальным номером."""
    return f"Предложение номер {n} рассказывает о чём-то важном и продолжается ещё немного, чтобы быть длинным."


def test_plain_text_keeps_short_continuous_speech_in_one_paragraph():
    # Границы декодера/VAD — не абзацы: фраза, разрезанная посередине, склеивается.
    utterances = [
        _utt("Первая часть фразы", 0.0, 5.0),
        _utt("  ", 5.0, 5.5),
        _utt("продолжается без разрыва.", 5.5, 9.0),
    ]
    assert formatters.generate_plain_text(utterances) == "Первая часть фразы продолжается без разрыва."


def test_plain_text_breaks_paragraph_on_pause_after_sentence_end():
    first = " ".join(_sentence(i) for i in range(3))
    utterances = [
        _utt(first, 0.0, 30.0),
        _utt("Новая мысль после паузы.", 33.0, 36.0),
    ]
    assert formatters.generate_plain_text(utterances) == f"{first}\n\nНовая мысль после паузы."


def test_plain_text_never_breaks_mid_sentence_even_on_long_pause():
    first = " ".join(_sentence(i) for i in range(3)) + " А потом он сказал, что"
    utterances = [_utt(first, 0.0, 30.0), _utt("всё получится.", 40.0, 42.0)]
    assert "\n" not in formatters.generate_plain_text(utterances)


def test_plain_text_ignores_pause_when_paragraph_is_still_short():
    utterances = [_utt("Короткая фраза.", 0.0, 2.0), _utt("Ещё одна.", 6.0, 7.0)]
    assert formatters.generate_plain_text(utterances) == "Короткая фраза. Ещё одна."


def test_plain_text_splits_long_monologue_at_sentence_ends():
    # Лекция без пауз: 40 предложений подряд в сегментах, режущих фразы посередине.
    text = " ".join(_sentence(i) for i in range(40))
    words = text.split(" ")
    utterances = [
        _utt(" ".join(words[i:i + 25]), i, i + 1.0) for i in range(0, len(words), 25)
    ]

    result = formatters.generate_plain_text(utterances)
    paragraphs = result.split("\n\n")

    assert len(paragraphs) > 3
    assert " ".join(paragraphs) == text
    assert all(p.endswith(".") and p[0].isupper() for p in paragraphs)
    assert all(len(p) <= formatters.PARAGRAPH_MAX_CHARS + 200 for p in paragraphs)


def test_plain_text_does_not_treat_abbreviation_as_sentence_end():
    text = " ".join(_sentence(i) for i in range(8)) + " Это т.е. одно и то же."
    utterances = [_utt(text, 0.0, 60.0)]
    for paragraph in formatters.generate_plain_text(utterances).split("\n\n"):
        assert not paragraph.startswith("одно")


def test_plain_text_without_punctuation_still_breaks_at_segment_boundaries():
    # CTC/RNNT-модели дают текст без пунктуации — абзацы режем по стыкам сегментов.
    chunk = "слова без знаков препинания идут одно за другим " * 5
    utterances = [_utt(chunk.strip(), i * 10.0, i * 10.0 + 9.0) for i in range(20)]

    paragraphs = formatters.generate_plain_text(utterances).split("\n\n")

    assert len(paragraphs) > 1
    assert " ".join(paragraphs) == " ".join(chunk.strip() for _ in range(20))


def test_diarized_text_blocks_per_speaker_with_paragraphs_inside():
    long_turn = " ".join(_sentence(i) for i in range(12))
    utterances = [
        {"transcription": long_turn[:500], "boundaries": (0.0, 30.0), "speaker": "Спикер №1"},
        {"transcription": long_turn[500:].strip(), "boundaries": (30.0, 70.0), "speaker": "Спикер №1"},
        {"transcription": "без метки, но тот же блок.", "boundaries": (70.0, 72.0), "speaker": None},
        {"transcription": "Короткий ответ.", "boundaries": (72.5, 74.0), "speaker": "Спикер №2"},
    ]

    text = formatters.generate_diarized_text(utterances)
    first, second = text.split("\n\n[Спикер №2]\n")

    assert first.startswith("[Спикер №1]\n")
    first_paragraphs = first.removeprefix("[Спикер №1]\n").split("\n\n")
    assert len(first_paragraphs) > 1
    assert first_paragraphs[-1].endswith("без метки, но тот же блок.")
    assert all(p[0].isupper() and "\n" not in p for p in first_paragraphs)
    assert second == "Короткий ответ."
