"""Генераторы выходных форматов транскрипции (TXT / SRT / VTT / Markdown).

Ранее были методами TranscriptionProcessor; вынесены в чистые функции, чтобы
формат-логику можно было тестировать и переиспользовать независимо от процессора.
TXT собирается в абзацы, Markdown сохраняет строку на сегмент, SRT/VTT используют
общий cue planner.
"""
from __future__ import annotations

import re

from .subtitles import SRT_SPEAKER_SEPARATOR, SubtitleOptions, build_subtitle_cues

# Абзацы обычного TXT. Границы сегментов (VAD/декодер) режут фразы посередине,
# поэтому абзац закрывается только на конце предложения: после заметной паузы
# или когда абзац уже длинный. Без пунктуации (CTC/RNNT) — на стыке сегментов.
PARAGRAPH_PAUSE_SEC = 2.0
PARAGRAPH_MIN_CHARS = 200
PARAGRAPH_MAX_CHARS = 700
# Конец предложения — только перед заглавной/цифрой: «т.е. одно» не делится.
_SENTENCE_END = re.compile(r"[.!?…][»\")]*(?=\s+[«\"(]?[A-ZА-ЯЁ0-9])")
_ENDS_SENTENCE = re.compile(r"[.!?…][»\")]*$")
_STARTS_SENTENCE = re.compile(r"[«\"(]?[A-ZА-ЯЁ0-9]")


def _split_sentences(text: str) -> list[str]:
    """Режет текст сегмента по концам предложений; последний кусок может быть оборван."""
    pieces, start = [], 0
    for match in _SENTENCE_END.finditer(text):
        pieces.append(text[start:match.end()].strip())
        start = match.end()
    pieces.append(text[start:].strip())
    return [piece for piece in pieces if piece]


def _paragraphs(segments: list[tuple[str, float, float]]) -> list[str]:
    """Собирает сегменты (text, start, end) одного говорящего в абзацы."""
    paragraphs: list[str] = []
    current: list[str] = []
    length = 0

    def flush() -> None:
        nonlocal current, length
        if current:
            paragraphs.append(" ".join(current))
        current, length = [], 0

    for index, (text, _, end) in enumerate(segments):
        pieces = _split_sentences(text)
        for piece_index, piece in enumerate(pieces):
            current.append(piece)
            length += len(piece) + 1
            if piece_index < len(pieces) - 1:
                # Внутри сегмента кусок кончается предложением по построению.
                if length >= PARAGRAPH_MAX_CHARS:
                    flush()
                continue
            if index + 1 == len(segments):
                continue
            next_text, next_start, _ = segments[index + 1]
            pause = next_start - end
            if _ENDS_SENTENCE.search(piece) and _STARTS_SENTENCE.match(next_text):
                if length >= PARAGRAPH_MAX_CHARS or (
                    pause >= PARAGRAPH_PAUSE_SEC and length >= PARAGRAPH_MIN_CHARS
                ):
                    flush()
            elif length >= 2 * PARAGRAPH_MAX_CHARS:
                # Давно нет конца предложения (текст без пунктуации) — стык сегментов
                # всё же лучше, чем бесконечная строка.
                flush()
    flush()
    return paragraphs


def _text_segments(utterances: list) -> list[tuple[str, float, float, str | None]]:
    segments = []
    for utt in utterances:
        text = (utt.get('transcription') or '').strip()
        if not text:
            continue
        start, end = utt.get('boundaries', (0.0, 0.0))
        segments.append((text, float(start), float(end), utt.get('speaker')))
    return segments


def generate_plain_text(utterances: list) -> str:
    """Обычный TXT: сплошной текст без таймкодов и спикеров, разбитый на абзацы."""
    segments = [segment[:3] for segment in _text_segments(utterances)]
    return "\n\n".join(_paragraphs(segments))


def generate_diarized_text(utterances: list) -> str:
    """TXT с метками спикеров: `[Спикер]` на каждую смену говорящего, затем его
    абзацы. Абзацы и блоки разделены пустой строкой, как в обычном TXT."""
    blocks: list[str] = []
    run: list[tuple[str, float, float]] = []
    run_speaker = None

    def close_run() -> None:
        if run:
            body = "\n\n".join(_paragraphs(run))
            blocks.append(f"[{run_speaker}]\n{body}" if run_speaker else body)

    for text, start, end, speaker in _text_segments(utterances):
        # Сегмент без метки остаётся в блоке текущего говорящего, как раньше.
        speaker = speaker or run_speaker
        if run and speaker != run_speaker:
            close_run()
            run = []
        run_speaker = speaker
        run.append((text, start, end))
    close_run()
    return "\n\n".join(blocks)


def format_timestamp(seconds: float, ms_sep: str) -> str:
    """Форматирует время как HH:MM:SS<ms_sep>mmm. ms_sep=',' для SRT, '.' для VTT."""
    total_millis = max(0, int(round(seconds * 1000)))
    total_seconds, millis = divmod(total_millis, 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{ms_sep}{millis:03d}"


def generate_srt(utterances: list, options: SubtitleOptions | None = None) -> str:
    """Генерирует контент в формате SRT субтитров.

    Метка спикера печатается только на первом cue реплики: SRT не переносит
    состояние между блоками, но читатель видит их последовательно, как в TXT/MD.
    Формат метки — обычный текст `Имя: `, без угловых скобок: `<Имя>` не является
    тегом SRT, ffmpeg показывает его буквально, а парсеры, режущие `<...>`,
    удаляют метку целиком. Долгая пауза того же спикера метку не повторяет.
    """
    lines = []

    for index, cue in enumerate(build_subtitle_cues(utterances, options), start=1):
        lines.append(str(index))
        lines.append(
            f"{format_timestamp(cue.start, ',')} --> {format_timestamp(cue.end, ',')}"
        )

        cue_lines = list(cue.lines)
        if cue.speaker_label and cue_lines:
            cue_lines[0] = (
                f"{cue.speaker_label}{SRT_SPEAKER_SEPARATOR}{cue_lines[0]}"
            )
        lines.extend(cue_lines)
        lines.append("")

    return "\n".join(lines)


def generate_vtt(utterances: list, options: SubtitleOptions | None = None) -> str:
    """Генерирует контент в формате VTT субтитров.

    Voice span <v ...> ставится на каждый cue: WebVTT не переносит состояние
    между cues, поэтому cue без него теряет атрибуцию спикера. Имя не обрезаем —
    оно невидимо в плеере и нужно для ::cue(v[voice="..."]) и внешних
    инструментов. Закрывающий </v> опускается по спецификации, потому что span
    занимает весь текст cue; при добавлении любой другой разметки в cue его
    придётся вернуть.
    """
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
