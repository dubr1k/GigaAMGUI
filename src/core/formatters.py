"""Генераторы выходных форматов транскрипции (SRT / VTT / Markdown).

Ранее были методами TranscriptionProcessor; вынесены в чистые функции, чтобы
формат-логику можно было тестировать и переиспользовать независимо от процессора.
TXT/Markdown сохраняют прежнюю структуру, SRT/VTT используют общий cue planner.
"""
from __future__ import annotations

from .subtitles import SubtitleOptions, build_subtitle_cues


def format_timestamp(seconds: float, ms_sep: str) -> str:
    """Форматирует время как HH:MM:SS<ms_sep>mmm. ms_sep=',' для SRT, '.' для VTT."""
    total_millis = max(0, int(round(seconds * 1000)))
    total_seconds, millis = divmod(total_millis, 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{ms_sep}{millis:03d}"


def generate_srt(utterances: list, options: SubtitleOptions | None = None) -> str:
    """Генерирует контент в формате SRT субтитров."""
    lines = []

    for index, cue in enumerate(build_subtitle_cues(utterances, options), start=1):
        lines.append(str(index))
        lines.append(
            f"{format_timestamp(cue.start, ',')} --> {format_timestamp(cue.end, ',')}"
        )

        cue_lines = list(cue.lines)
        if cue.speaker and cue_lines:
            cue_lines[0] = f"<{cue.speaker}> {cue_lines[0]}"
        lines.extend(cue_lines)
        lines.append("")

    return "\n".join(lines)


def generate_vtt(utterances: list, options: SubtitleOptions | None = None) -> str:
    """Генерирует контент в формате VTT субтитров."""
    lines = ["WEBVTT", ""]

    for cue in build_subtitle_cues(utterances, options):
        lines.append(
            f"{format_timestamp(cue.start, '.')} --> {format_timestamp(cue.end, '.')}"
        )

        cue_lines = list(cue.lines)
        if cue.speaker and cue_lines:
            cue_lines[0] = f"<v {cue.speaker}>{cue_lines[0]}"
        lines.extend(cue_lines)
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
