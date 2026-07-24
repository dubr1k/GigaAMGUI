"""Характеризующие тесты форматтеров SRT/VTT/Markdown."""
from src.core import formatters
from src.core.subtitles import SubtitleOptions


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
        "<SPEAKER_00> второй третий\n"
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
    assert "<1> Первая короткая\nфраза." in srt
    assert "<v 1>Первая короткая\nфраза." in vtt


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
    vtt_payload = [
        line for line in vtt.splitlines()
        if line and line != "WEBVTT" and "-->" not in line
    ]
    assert all(len(line) <= 20 for line in [*srt_payload, *vtt_payload])


def test_generate_markdown_speaker_header_once():
    md = formatters.generate_markdown(UTTS, "audio.mp3", _TF())
    assert md.startswith("# Транскрипция: audio.mp3")
    assert "*Создано с помощью GigaAM v3 Transcriber*" in md
    # заголовок спикера появляется один раз для двух подряд реплик одного спикера
    assert md.count("### SPEAKER_00") == 1
    assert "`01:05 - 01:10`" in md
