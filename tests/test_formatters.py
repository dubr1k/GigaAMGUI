"""Характеризующие тесты форматтеров — фиксируют вывод SRT/VTT/MD 1:1."""
from src.core import formatters


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
        "00:01:05,250 --> 00:01:10,000\n"
        "<SPEAKER_00> второй\n"
        "\n"
        "3\n"
        "00:01:10,000 --> 00:01:12,000\n"
        "<SPEAKER_00> третий\n"
    )


def test_generate_vtt():
    vtt = formatters.generate_vtt(UTTS)
    assert vtt.startswith("WEBVTT\n\n")
    assert "00:00:00.000 --> 00:00:01.500" in vtt
    assert "<v SPEAKER_00>второй" in vtt
    # пустой сегмент пропущен
    assert vtt.count("-->") == 3


def test_generate_markdown_speaker_header_once():
    md = formatters.generate_markdown(UTTS, "audio.mp3", _TF())
    assert md.startswith("# Транскрипция: audio.mp3")
    assert "*Создано с помощью GigaAM v3 Transcriber*" in md
    # заголовок спикера появляется один раз для двух подряд реплик одного спикера
    assert md.count("### SPEAKER_00") == 1
    assert "`01:05 - 01:10`" in md
