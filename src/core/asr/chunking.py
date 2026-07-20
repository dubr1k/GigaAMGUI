"""Общее безопасное разбиение long-form аудио для ASR backend-ов."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

import numpy as np

from .types import TranscriptionWord


@dataclass(frozen=True)
class AudioChunk:
    """Окно декодирования и его неперекрывающаяся временная область."""

    group: int
    decode_start_sample: int
    decode_end_sample: int
    start_sec: float
    end_sec: float
    overlaps_previous: bool = False


def _as_mono_array(audio: Any) -> np.ndarray:
    if hasattr(audio, "detach"):
        audio = audio.detach().cpu().numpy()
    values = np.asarray(audio, dtype=np.float32)
    if values.ndim > 1:
        values = values.mean(axis=0)
    return values.reshape(-1)


def _lowest_energy_cut(
    audio: np.ndarray,
    lower: int,
    upper: int,
    *,
    energy_window_samples: int,
    preferred_sample: int,
) -> int:
    """Найти центр самого тихого окна без дорогой свёртки."""

    lower = max(0, int(lower))
    upper = min(len(audio), int(upper))
    if upper <= lower:
        return lower

    width = max(1, min(int(energy_window_samples), upper - lower))
    values = np.abs(audio[lower:upper]).astype(np.float64, copy=False)
    if len(values) <= width:
        return lower + len(values) // 2

    cumulative = np.concatenate(([0.0], np.cumsum(values)))
    energies = (cumulative[width:] - cumulative[:-width]) / width
    minimum = float(np.min(energies))
    candidates = np.flatnonzero(np.isclose(energies, minimum, rtol=1e-6, atol=1e-12))
    preferred_index = preferred_sample - lower - width // 2
    best = int(candidates[np.argmin(np.abs(candidates - preferred_index))])
    return lower + best + width // 2


def plan_audio_chunks(
    audio: Any,
    regions: list[tuple[float, float]],
    *,
    sample_rate: int,
    max_chunk_seconds: float,
    overlap_seconds: float = 2.0,
    search_seconds: float = 2.0,
    energy_window_seconds: float = 0.25,
    min_chunk_seconds: float = 5.0,
) -> list[AudioChunk]:
    """Спланировать ограниченные окна, не создавая разрывов во времени.

    Короткие VAD-регионы передаются декодеру без изменений. Длинные регионы
    делятся около локальных минимумов энергии. Окна декодирования перекрываются,
    а ``start_sec/end_sec`` остаются смежными и не перекрываются — это сохраняет
    корректные таймкоды и даёт контекст по обе стороны вынужденного разреза.
    """

    if sample_rate <= 0:
        raise ValueError("sample_rate должен быть положительным")
    if max_chunk_seconds <= 0:
        raise ValueError("max_chunk_seconds должен быть положительным")
    if overlap_seconds < 0 or overlap_seconds >= max_chunk_seconds:
        raise ValueError("overlap_seconds должен быть меньше max_chunk_seconds")

    values = _as_mono_array(audio)
    total_samples = len(values)
    max_samples = max(1, int(max_chunk_seconds * sample_rate))
    overlap_samples = max(0, int(overlap_seconds * sample_rate))
    nominal_limit = max(1, max_samples - overlap_samples)
    half_overlap = overlap_samples // 2
    search_samples = max(0, int(search_seconds * sample_rate))
    energy_window_samples = max(1, int(energy_window_seconds * sample_rate))
    configured_min_samples = max(1, int(min_chunk_seconds * sample_rate))
    chunks: list[AudioChunk] = []

    for group, (raw_start, raw_end) in enumerate(regions):
        boundary_start = max(0.0, float(raw_start))
        boundary_end = min(float(total_samples) / sample_rate, float(raw_end))
        region_start = max(0, int(boundary_start * sample_rate))
        region_end = min(total_samples, int(boundary_end * sample_rate))
        duration = region_end - region_start
        if duration <= 0:
            continue

        if duration <= max_samples:
            chunks.append(
                AudioChunk(
                    group=group,
                    decode_start_sample=region_start,
                    decode_end_sample=region_end,
                    start_sec=boundary_start,
                    end_sec=boundary_end,
                )
            )
            continue

        part_count = max(2, math.ceil(duration / nominal_limit))
        ideal_part = duration / part_count
        min_samples = max(1, min(configured_min_samples, int(ideal_part / 2)))
        cuts: list[int] = []
        previous = region_start

        for index in range(1, part_count):
            remaining_parts = part_count - index
            target = int(region_start + duration * index / part_count)
            feasible_lower = max(
                previous + min_samples,
                region_end - remaining_parts * nominal_limit,
            )
            feasible_upper = min(
                previous + nominal_limit,
                region_end - remaining_parts * min_samples,
            )
            search_lower = max(feasible_lower, target - search_samples)
            search_upper = min(feasible_upper, target + search_samples)
            if search_upper <= search_lower:
                cut = max(feasible_lower, min(target, feasible_upper))
            else:
                cut = _lowest_energy_cut(
                    values,
                    search_lower,
                    search_upper,
                    energy_window_samples=energy_window_samples,
                    preferred_sample=target,
                )
                cut = max(feasible_lower, min(cut, feasible_upper))
            cuts.append(cut)
            previous = cut

        nominal_boundaries = [region_start, *cuts, region_end]
        for index, (nominal_start, nominal_end) in enumerate(
            zip(nominal_boundaries, nominal_boundaries[1:], strict=False)
        ):
            decode_start = nominal_start if index == 0 else nominal_start - half_overlap
            decode_end = (
                nominal_end
                if index == len(nominal_boundaries) - 2
                else nominal_end + (overlap_samples - half_overlap)
            )
            decode_start = max(region_start, decode_start)
            decode_end = min(region_end, decode_end)
            if decode_end - decode_start > max_samples:
                decode_end = decode_start + max_samples

            chunks.append(
                AudioChunk(
                    group=group,
                    decode_start_sample=decode_start,
                    decode_end_sample=decode_end,
                    start_sec=(
                        boundary_start
                        if index == 0
                        else float(nominal_start) / sample_rate
                    ),
                    end_sec=(
                        boundary_end
                        if index == len(nominal_boundaries) - 2
                        else float(nominal_end) / sample_rate
                    ),
                    overlaps_previous=index > 0,
                )
            )

    return chunks


_WORD_RE = re.compile(r"[\wЁё]+(?:[-'][\wЁё]+)*", re.UNICODE)
_TRAILING_CONTINUATION_RE = re.compile(r"(?:\s*(?:\.\.\.|…))+\s*$")


def _word_spans(text: str) -> list[tuple[str, int, int]]:
    result = []
    for match in _WORD_RE.finditer(text):
        normalized = unicodedata.normalize("NFKC", match.group(0)).casefold().replace("ё", "е")
        result.append((normalized, match.start(), match.end()))
    return result


def _aligned_tail_prefix_overlap(
    previous_words: list[tuple[str, int, int]],
    current_words: list[tuple[str, int, int]],
) -> tuple[int, int]:
    """Найти overlap при небольших вставках/заменах около границы окна."""

    left = [item[0] for item in previous_words[-24:]]
    right = [item[0] for item in current_words[:24]]
    if not left or not right:
        return 0, 0

    # (matched, meaningful, total_gaps, previous_start, current_start)
    paths: dict[tuple[int, int], tuple[int, int, int, int, int]] = {}
    for previous_index, previous_word in enumerate(left):
        for current_index, current_word in enumerate(right):
            if previous_word != current_word:
                continue

            candidates: list[tuple[int, int, int, int, int]] = []
            if current_index <= 2:
                candidates.append(
                    (
                        1,
                        int(len(previous_word) >= 3),
                        0,
                        previous_index,
                        current_index,
                    )
                )

            for previous_step in range(1, 4):
                for current_step in range(1, 4):
                    predecessor = paths.get(
                        (previous_index - previous_step, current_index - current_step)
                    )
                    if predecessor is None:
                        continue
                    matched, meaningful, gaps, previous_start, current_start = predecessor
                    candidates.append(
                        (
                            matched + 1,
                            meaningful + int(len(previous_word) >= 3),
                            gaps + previous_step + current_step - 2,
                            previous_start,
                            current_start,
                        )
                    )

            if candidates:
                paths[(previous_index, current_index)] = max(
                    candidates,
                    key=lambda item: (
                        item[1],
                        item[0],
                        item[3],
                        -item[2],
                        -item[4],
                    ),
                )

    accepted: list[tuple[int, int, int, int, int, int]] = []
    previous_end = len(left) - 1
    for (previous_index, current_index), path in paths.items():
        if previous_index != previous_end:
            continue
        matched, meaningful, gaps, previous_start, current_start = path
        strong_alignment = meaningful >= 3
        two_word_alignment = (
            matched == meaningful == 2
            and gaps <= 1
            and len(left[previous_start]) >= 4
            and len(left[previous_end]) >= 4
        )
        if strong_alignment or two_word_alignment:
            accepted.append(
                (
                    current_index + 1,
                    matched,
                    meaningful,
                    gaps,
                    previous_start,
                    current_start,
                )
            )

    if not accepted:
        return 0, 0

    trim_words, matched, _, _, _, _ = max(
        accepted,
        key=lambda item: (
            item[2],
            item[1],
            item[4],
            -item[3],
            -item[5],
        ),
    )
    return trim_words, matched


def stitch_overlapping_text(previous: str, current: str) -> tuple[str, str, int]:
    """Убрать повтор начала нового окна, распознанный в аудио-перекрытии."""

    previous_words = _word_spans(previous)
    current_words = _word_spans(current)
    limit = min(32, len(previous_words), len(current_words))
    overlap = 0
    trim_words = 0

    for size in range(limit, 0, -1):
        left = [item[0] for item in previous_words[-size:]]
        right = [item[0] for item in current_words[:size]]
        exact = left == right
        single_word_match = size == 1 and len(left[0]) >= 4
        fuzzy = size >= 3 and SequenceMatcher(
            None,
            " ".join(left),
            " ".join(right),
        ).ratio() >= 0.86
        if (exact and (size > 1 or single_word_match)) or fuzzy:
            overlap = size
            trim_words = size
            break

    if overlap == 0:
        trim_words, overlap = _aligned_tail_prefix_overlap(
            previous_words,
            current_words,
        )
    if overlap == 0:
        return previous, current, 0

    if trim_words < len(current_words):
        cut = current_words[trim_words][1]
    else:
        cut = len(current)
    trimmed = current[cut:].lstrip(" \t\r\n,.;:!?…—-")
    cleaned_previous = _TRAILING_CONTINUATION_RE.sub("", previous).rstrip()
    # Третьим значением возвращается число слов, реально удалённых из текста, а
    # не число совпавших: при вставке между совпавшими блоками из текста уходят
    # ещё и слова вставки. Потребители режут этим значением список words, и
    # рассинхрон приводил к дублю слова на стыке либо к потере таймкодов.
    return cleaned_previous, trimmed, trim_words


def normalize_chunk_words(
    words: list[TranscriptionWord] | None,
    *,
    start_sec: float,
    end_sec: float,
    trim_prefix_words: int = 0,
) -> list[TranscriptionWord] | None:
    """Оставить слова текущего чанка только в его номинальном интервале."""

    if not words:
        return None
    if not math.isfinite(start_sec) or not math.isfinite(end_sec) or end_sec < start_sec:
        raise ValueError("Границы чанка должны быть конечными и упорядоченными")
    if trim_prefix_words < 0:
        raise ValueError("trim_prefix_words не может быть отрицательным")

    validated: list[tuple[str, float, float]] = []
    for word in words:
        try:
            start = float(word["start"])
            end = float(word["end"])
            text = str(word["text"])
        except (KeyError, TypeError, ValueError):
            return None
        if not math.isfinite(start) or not math.isfinite(end) or end < start:
            return None
        validated.append((text, start, end))

    normalized: list[TranscriptionWord] = []
    previous_end = start_sec
    for text, start, end in validated[trim_prefix_words:]:
        clipped_start = max(start_sec, previous_end, start)
        clipped_end = min(end_sec, end)
        if clipped_end <= clipped_start:
            continue
        normalized.append(
            {
                "text": text,
                "start": clipped_start,
                "end": clipped_end,
            }
        )
        previous_end = clipped_end

    return normalized
