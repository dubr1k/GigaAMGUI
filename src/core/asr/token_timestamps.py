"""Преобразование token timestamps ONNX ASR в слова приложения."""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Sequence

from .types import TranscriptionWord

_LAST_TOKEN_DURATION_SECONDS = 0.04


def _is_punctuation(piece: str) -> bool:
    return bool(piece) and all(
        char.isspace() or unicodedata.category(char).startswith(("P", "S"))
        for char in piece
    )


def tokens_to_words(
    tokens: Sequence[str] | None,
    timestamps: Sequence[float] | None,
    *,
    duration: float,
) -> list[TranscriptionWord] | None:
    """Собрать слова из SentencePiece-токенов и времён их эмиссии."""

    if tokens is None or timestamps is None or len(tokens) != len(timestamps):
        return None

    safe_duration = float(duration)
    if not math.isfinite(safe_duration) or safe_duration < 0.0:
        return None

    grouped: list[dict[str, str | float]] = []
    previous_timestamp = 0.0
    pending_word_boundary = False

    for raw_token, raw_timestamp in zip(tokens, timestamps, strict=True):
        token = str(raw_token)
        if not token:
            continue

        timestamp = float(raw_timestamp)
        if not math.isfinite(timestamp):
            return None
        timestamp = min(safe_duration, max(0.0, timestamp, previous_timestamp))
        previous_timestamp = timestamp

        starts_word = pending_word_boundary or token.startswith((" ", "▁"))
        piece = token.lstrip(" ▁") if starts_word else token
        if not piece:
            if token.isspace() or "▁" in token:
                pending_word_boundary = True
            continue
        pending_word_boundary = False

        if _is_punctuation(piece) and grouped:
            grouped[-1]["text"] = f"{grouped[-1]['text']}{piece}"
            grouped[-1]["last"] = timestamp
            continue

        if starts_word or not grouped:
            grouped.append({"text": piece, "start": timestamp, "last": timestamp})
        else:
            grouped[-1]["text"] = f"{grouped[-1]['text']}{piece}"
            grouped[-1]["last"] = timestamp

    words: list[TranscriptionWord] = []
    for index, item in enumerate(grouped):
        start = float(item["start"])
        if index + 1 < len(grouped):
            end = float(grouped[index + 1]["start"])
        else:
            end = min(
                safe_duration,
                max(start, float(item["last"]) + _LAST_TOKEN_DURATION_SECONDS),
            )
        words.append(
            {
                "text": str(item["text"]),
                "start": round(start, 9),
                "end": round(max(start, end), 9),
            }
        )
    return words
