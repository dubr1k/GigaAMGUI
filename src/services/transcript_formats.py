"""Сборка ответов в формате OpenAI Audio API из списка реплик процессора.

Чистые функции без FastAPI — api.py только выбирает формат и media type.
"""
from __future__ import annotations

import math
from typing import Any

from src.core import formatters
from src.core.subtitles import SubtitleOptions

Utterance = dict[str, Any]

FORMATS = ("json", "text", "srt", "vtt", "verbose_json", "diarized_json")
MEDIA_TYPES = {
    "json": "application/json",
    "verbose_json": "application/json",
    "diarized_json": "application/json",
    "text": "text/plain; charset=utf-8",
    "srt": "application/x-subrip",
    "vtt": "text/vtt",
}
DEFAULT_LANGUAGE = "ru"


def _bounds(utt: Utterance) -> tuple[float, float]:
    start, end = utt.get("boundaries", (0.0, 0.0))
    return float(start), float(end)


def full_text(utts: list[Utterance]) -> str:
    return " ".join(part for part in (u.get("transcription", "").strip() for u in utts) if part)


def usage(duration: float) -> dict[str, Any]:
    return {"type": "duration", "seconds": int(math.ceil(max(float(duration or 0.0), 0.0)))}


def speaker_letters(utts: list[Utterance]) -> dict[str, str]:
    """SPEAKER_00 → 'A' по порядку первого появления, как в diarized_json OpenAI."""
    letters: dict[str, str] = {}
    for utt in utts:
        speaker = utt.get("speaker")
        if speaker and speaker not in letters:
            letters[speaker] = _letter(len(letters))
    return letters


def _letter(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(ord("A") + rem) + name
    return name


def build_json(utts: list[Utterance], duration: float) -> dict[str, Any]:
    return {"text": full_text(utts), "usage": usage(duration)}


def build_text(utts: list[Utterance]) -> str:
    return full_text(utts)


def build_verbose(
    utts: list[Utterance],
    duration: float,
    language: str | None,
    granularities: set[str],
    diarized: bool,
) -> dict[str, Any]:
    letters = speaker_letters(utts) if diarized else {}
    segments = []
    words: list[dict[str, Any]] = []
    for index, utt in enumerate(utts):
        start, end = _bounds(utt)
        segment: dict[str, Any] = {
            "id": index,
            "seek": 0,
            "start": start,
            "end": end,
            "text": utt.get("transcription", ""),
            "tokens": [],
            "temperature": 0.0,
            "avg_logprob": 0.0,
            "compression_ratio": 0.0,
            "no_speech_prob": 0.0,
        }
        if diarized:
            segment["speaker"] = letters.get(utt.get("speaker"), "A")
        segments.append(segment)
        for word in utt.get("words") or []:
            words.append({"word": word["text"], "start": float(word["start"]), "end": float(word["end"])})
    out: dict[str, Any] = {
        "task": "transcribe",
        "language": language or DEFAULT_LANGUAGE,
        "duration": float(duration or 0.0),
        "text": full_text(utts),
        "segments": segments,
    }
    if "word" in granularities:
        out["words"] = words
    return out


def build_diarized(utts: list[Utterance], duration: float) -> dict[str, Any]:
    letters = speaker_letters(utts)
    segments = []
    for index, utt in enumerate(utts):
        start, end = _bounds(utt)
        segments.append({
            "id": index,
            "type": "transcript.text.segment",
            "start": start,
            "end": end,
            "speaker": letters.get(utt.get("speaker"), "A"),
            "text": utt.get("transcription", ""),
        })
    return {"task": "transcribe", "duration": float(duration or 0.0), "text": full_text(utts), "segments": segments}


def build_srt(utts: list[Utterance], options: SubtitleOptions | None = None) -> str:
    return formatters.generate_srt(utts, options)


def build_vtt(utts: list[Utterance], options: SubtitleOptions | None = None) -> str:
    return formatters.generate_vtt(utts, options)


def render(
    fmt: str,
    utts: list[Utterance],
    duration: float,
    *,
    language: str | None,
    granularities: set[str],
    diarized: bool,
    subtitle_options: SubtitleOptions | None,
) -> tuple[str | dict[str, Any], str]:
    if fmt not in MEDIA_TYPES:
        raise ValueError(f"unsupported response_format: {fmt}")
    if fmt == "json":
        body: str | dict[str, Any] = build_json(utts, duration)
    elif fmt == "verbose_json":
        body = build_verbose(utts, duration, language, granularities, diarized)
    elif fmt == "diarized_json":
        body = build_diarized(utts, duration)
    elif fmt == "text":
        body = build_text(utts)
    elif fmt == "srt":
        body = build_srt(utts, subtitle_options)
    else:
        body = build_vtt(utts, subtitle_options)
    return body, MEDIA_TYPES[fmt]
