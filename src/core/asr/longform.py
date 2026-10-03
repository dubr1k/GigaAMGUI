"""Общие части long-form распознавания для ASR backend-ов.

Все три backend-а (PyTorch, ONNX, MLX) режут файл на окна одинаково
(``chunking.plan_audio_chunks``) и одинаково собирают из окон сегменты:
сшивка перекрытий, обрезка слов по номинальному интервалу окна, монотонный
прогресс. Цикл сборки живёт здесь один раз; backend-ы отличаются только
декодированием окна и параметрами (минимальная длина окна, округление
времён слов).
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Callable, Hashable, Iterable, Iterator
from contextlib import contextmanager
from typing import Any

from ...utils.cancellation import CancelCheck, raise_if_cancelled
from ..progress import ProgressCallback
from .chunking import AudioChunk, normalize_chunk_words, stitch_chunk
from .types import TranscriptionSegment, TranscriptionWord
from .vad import VadSegmenter, VadUnavailableError

# Декодировать окно: (текст, слова с абсолютными временами или None).
DecodeWindow = Callable[[AudioChunk], "tuple[str, list[TranscriptionWord] | None]"]

# Неудачную инициализацию VAD не повторяем на каждом файле (батч иначе качал
# бы недоступную модель снова и снова), но и не помним до перезапуска:
# на долгоживущем сервере один сетевой сбой отключал VAD навсегда.
VAD_RETRY_COOLDOWN_SECONDS = 300.0


def _now() -> float:
    return time.monotonic()


class VadFailureMemo:
    """Последний сбой инициализации VAD с ключом окружения и временем."""

    def __init__(self) -> None:
        self.clear()

    def clear(self) -> None:
        self._key: Hashable | None = None
        self._reason: str | None = None
        self._failed_at: float | None = None

    def remember(self, key: Hashable, reason: str) -> None:
        self._key = key
        self._reason = reason
        self._failed_at = _now()

    def blocked(self, key: Hashable) -> str | None:
        """Причина недавнего сбоя для этого ключа или None — можно пробовать."""
        if self._failed_at is None or self._key != key:
            return None
        if _now() - self._failed_at >= VAD_RETRY_COOLDOWN_SECONDS:
            return None
        return self._reason


def pyannote_vad_key(device: str) -> tuple[str | None, tuple[bytes, str]]:
    """(HF-токен, ключ кэша): сегментер пересоздаётся при смене токена или устройства."""
    token = os.getenv("HF_TOKEN", "").strip() or None
    fingerprint = hashlib.sha256((token or "").encode()).digest()
    return token, (fingerprint, device)


class VadSegmenterCache:
    """Сегментер VAD для pyannote-backend-ов (PyTorch, MLX).

    Сегментер переиспользуется, пока не сменился ключ (токен, устройство).
    Любой сбой сегментации сбрасывает его; сбой инициализации
    (``VadUnavailableError``) запоминается на ``VAD_RETRY_COOLDOWN_SECONDS``.
    """

    def __init__(self, factory: Callable[..., VadSegmenter]):
        self._factory = factory
        self.segmenter: VadSegmenter | None = None
        self.key: Hashable | None = None
        self.failure = VadFailureMemo()

    def segment(
        self,
        key: Hashable,
        audio_path: str,
        *,
        audio_duration: float,
        **factory_kwargs: Any,
    ) -> list[tuple[float, float]]:
        if self.failure.blocked(key) is not None:
            # Не запоминаем этот отказ заново: иначе пауза перед повтором
            # продлевалась бы каждым файлом и никогда не истекала.
            raise VadUnavailableError("previous VAD initialization failed")
        try:
            if self.segmenter is None or self.key != key:
                self.segmenter = self._factory(**factory_kwargs)
                self.key = key
                self.failure.clear()
            return self.segmenter.segment_file(audio_path, audio_duration=audio_duration)
        except Exception as exc:
            self.segmenter = None
            self.key = None
            if isinstance(exc, VadUnavailableError):
                self.failure.remember(key, str(exc))
            else:
                self.failure.clear()
            raise

    def reset(self) -> None:
        self.segmenter = None
        self.key = None
        self.failure.clear()


def assemble_segments(
    chunks: Iterable[AudioChunk],
    decode: DecodeWindow,
    *,
    total_seconds: float,
    min_chunk_samples: int = 0,
    progress_callback: ProgressCallback | None = None,
    on_chunk_done: Callable[[int], None] | None = None,
    cancel_check: CancelCheck | None = None,
) -> list[TranscriptionSegment]:
    """Распознать окна по порядку и собрать сегменты без повторов на стыках.

    Окна одной VAD-группы перекрываются по звуку; повтор начала окна
    сшивается с предыдущим сегментом (``stitch_chunk``), а слова обрезаются
    по номинальному интервалу окна. Если от окна после сшивки не осталось
    текста, предыдущий сегмент растягивается до конца окна. Прогресс
    монотонный: (доля, обработано секунд, всего секунд).
    """
    results: list[TranscriptionSegment] = []
    previous_index: int | None = None
    previous_group: int | None = None
    reported = 0.0

    for index, chunk in enumerate(chunks):
        # Отмена между окнами: окно (до 30 с звука) декодируется секунды.
        raise_if_cancelled(cancel_check)
        if chunk.decode_end_sample - chunk.decode_start_sample < min_chunk_samples:
            continue

        text, words = decode(chunk)
        text = str(text or "").strip()
        if words:
            text = " ".join(word["text"] for word in words).strip()

        if text:
            overlap_words = 0
            if (
                chunk.overlaps_previous
                and previous_index is not None
                and previous_group == chunk.group
            ):
                previous = results[previous_index]
                stitched = stitch_chunk(
                    previous["transcription"],
                    previous.get("words"),
                    text,
                    words,
                )
                previous["transcription"] = stitched.previous_text
                if stitched.previous_words is not None:
                    previous["words"] = stitched.previous_words
                text = stitched.text
                overlap_words = stitched.trim_words

            start_time = max(0.0, float(chunk.start_sec))
            end_time = min(total_seconds, float(chunk.end_sec))
            if end_time < start_time:
                continue
            if words is not None:
                words = normalize_chunk_words(
                    words,
                    start_sec=start_time,
                    end_sec=end_time,
                    trim_prefix_words=overlap_words,
                )
                if words is not None:
                    text = " ".join(word["text"] for word in words).strip()
            if not text and overlap_words and previous_index is not None:
                previous_start, _previous_end = results[previous_index]["boundaries"]
                results[previous_index]["boundaries"] = (previous_start, end_time)
            if text:
                segment: TranscriptionSegment = {
                    "transcription": text,
                    "boundaries": (start_time, end_time),
                }
                if words is not None:
                    segment["words"] = words
                results.append(segment)
                previous_index = len(results) - 1
                previous_group = chunk.group
        else:
            previous_index = None
            previous_group = None

        processed_seconds = min(total_seconds, float(chunk.end_sec))
        ratio = 1.0 if total_seconds <= 0 else min(processed_seconds / total_seconds, 1.0)
        if progress_callback is not None and ratio >= reported:
            progress_callback(ratio, processed_seconds, total_seconds)
            reported = ratio
        if on_chunk_done is not None:
            on_chunk_done(index)

    if progress_callback is not None and total_seconds > 0 and reported < 1.0:
        progress_callback(1.0, total_seconds, total_seconds)
    return results


def absolute_words(
    relative_words: list[TranscriptionWord] | None,
    offset_seconds: float,
    *,
    digits: int | None = None,
) -> list[TranscriptionWord] | None:
    """Сдвинуть времена слов окна на его начало (ONNX/MLX округляют до 9 знаков)."""
    if relative_words is None:
        return None

    def shift(value: float) -> float:
        moved = offset_seconds + value
        return round(moved, digits) if digits is not None else moved

    return [
        {"text": word["text"], "start": shift(word["start"]), "end": shift(word["end"])}
        for word in relative_words
    ]


@contextmanager
def call_logger(backend: Any, logger: Callable[[str], None] | None) -> Iterator[None]:
    """Направить предупреждения backend-а в журнал текущего вызова.

    ``load()`` запоминает logger, а повторный ``load_model()`` при уже
    загруженной модели возвращается сразу. Без этой подмены предупреждения
    всех следующих файлов (резервное разбиение без VAD и т.п.) уходили в
    журнал задачи, которая когда-то загрузила модель. Вызывается под
    inference-lock backend-а, поэтому параллельные вызовы друг другу не мешают.
    """
    previous = getattr(backend, "_logger", None)
    if logger is not None:
        backend._logger = logger
    try:
        yield
    finally:
        backend._logger = previous
