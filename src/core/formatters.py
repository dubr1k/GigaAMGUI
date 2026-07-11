"""Генераторы выходных форматов транскрипции (SRT / VTT / Markdown).

Ранее были методами TranscriptionProcessor; вынесены в чистые функции, чтобы
формат-логику можно было тестировать и переиспользовать независимо от процессора.
Поведение сохранено 1:1.
"""
from __future__ import annotations


def format_timestamp(seconds: float, ms_sep: str) -> str:
    """Форматирует время как HH:MM:SS<ms_sep>mmm. ms_sep=',' для SRT, '.' для VTT."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{ms_sep}{millis:03d}"


def generate_srt(utterances: list) -> str:
    """Генерирует контент в формате SRT субтитров."""
    lines = []
    index = 1

    for utt in utterances:
        text = utt.get('transcription', '')
        if not text or not text.strip():
            continue

        boundaries = utt.get('boundaries', (0.0, 0.0))
        start, end = boundaries
        speaker = utt.get('speaker', None)

        lines.append(str(index))
        lines.append(f"{format_timestamp(start, ',')} --> {format_timestamp(end, ',')}")

        if speaker:
            lines.append(f"<{speaker}> {text}")
        else:
            lines.append(text)

        lines.append("")
        index += 1

    return "\n".join(lines)


def generate_vtt(utterances: list) -> str:
    """Генерирует контент в формате VTT субтитров."""
    lines = ["WEBVTT", ""]

    for utt in utterances:
        text = utt.get('transcription', '')
        if not text or not text.strip():
            continue

        boundaries = utt.get('boundaries', (0.0, 0.0))
        start, end = boundaries
        speaker = utt.get('speaker', None)

        lines.append(f"{format_timestamp(start, '.')} --> {format_timestamp(end, '.')}")

        if speaker:
            lines.append(f"<v {speaker}>{text}")
        else:
            lines.append(text)

        lines.append("")

    return "\n".join(lines)


def generate_markdown(utterances: list, filename: str, time_formatter) -> str:
    """Генерирует контент в формате Markdown.

    time_formatter — объект с методом format_timestamp(seconds) для человекочитаемого
    времени (передаётся вызывающей стороной, обычно TimeFormatter).
    """
    lines = [
        f"# Транскрипция: {filename}",
        "",
        "*Создано с помощью GigaAM v3 Transcriber*",
        "",
        "---",
        "",
    ]

    current_speaker = None

    for utt in utterances:
        text = utt.get('transcription', '')
        if not text or not text.strip():
            continue

        boundaries = utt.get('boundaries', (0.0, 0.0))
        start, end = boundaries
        speaker = utt.get('speaker', None)

        time_str = f"`{time_formatter.format_timestamp(start)} - {time_formatter.format_timestamp(end)}`"

        if speaker:
            if speaker != current_speaker:
                lines.append("")
                lines.append(f"### {speaker}")
                lines.append("")
                current_speaker = speaker

            lines.append(f"- {time_str} {text}")
        else:
            lines.append(f"- {time_str} {text}")

    return "\n".join(lines)
