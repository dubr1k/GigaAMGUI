"""Регрессии безопасного overlap-разбиения long-form ASR."""

import numpy as np
import pytest

from src.core.asr import chunking
from src.core.asr.chunking import plan_audio_chunks, stitch_overlapping_text


def test_short_vad_region_preserves_exact_boundaries():
    audio = np.zeros(25 * 100, dtype=np.float32)

    chunks = plan_audio_chunks(
        audio,
        [(3.11909375, 19.65659375)],
        sample_rate=100,
        max_chunk_seconds=20.0,
    )

    assert len(chunks) == 1
    assert chunks[0].start_sec == 3.11909375
    assert chunks[0].end_sec == 19.65659375
    assert chunks[0].overlaps_previous is False


def test_long_region_uses_low_energy_cuts_and_overlapping_decode_windows():
    sample_rate = 100
    audio = np.ones(45 * sample_rate, dtype=np.float32)
    audio[1550:1650] = 0.0
    audio[3150:3250] = 0.0

    chunks = plan_audio_chunks(
        audio,
        [(0.0, 45.0)],
        sample_rate=sample_rate,
        max_chunk_seconds=20.0,
        overlap_seconds=2.0,
        search_seconds=2.0,
    )

    assert len(chunks) == 3
    assert chunks[0].end_sec == pytest.approx(16.0, abs=0.6)
    assert chunks[1].end_sec == pytest.approx(32.0, abs=0.6)
    assert [chunk.start_sec for chunk in chunks[1:]] == pytest.approx(
        [chunk.end_sec for chunk in chunks[:-1]]
    )
    assert chunks[1].decode_start_sample < int(chunks[1].start_sec * sample_rate)
    assert chunks[0].decode_end_sample > int(chunks[0].end_sec * sample_rate)
    assert all(
        chunk.decode_end_sample - chunk.decode_start_sample <= 20 * sample_rate
        for chunk in chunks
    )


def test_continuous_speech_has_no_gaps_and_no_tiny_tail():
    sample_rate = 100
    audio = np.ones(41 * sample_rate, dtype=np.float32)

    chunks = plan_audio_chunks(
        audio,
        [(0.0, 41.0)],
        sample_rate=sample_rate,
        max_chunk_seconds=20.0,
        overlap_seconds=2.0,
    )

    assert chunks[0].start_sec == 0.0
    assert chunks[-1].end_sec == 41.0
    assert [chunk.start_sec for chunk in chunks[1:]] == pytest.approx(
        [chunk.end_sec for chunk in chunks[:-1]]
    )
    assert min(chunk.end_sec - chunk.start_sec for chunk in chunks) >= 10.0
    assert all(
        chunk.decode_end_sample - chunk.decode_start_sample <= 20 * sample_rate
        for chunk in chunks
    )


def test_stitch_overlap_removes_duplicate_prefix_and_artificial_ellipsis():
    previous, current, overlap = stitch_overlapping_text(
        "Стоимость поездки будет девятьсот восемьдесят рублей...",
        "девятьсот восемьдесят рублей. Спасибо, всего доброго.",
    )

    assert previous == "Стоимость поездки будет девятьсот восемьдесят рублей"
    assert current == "Спасибо, всего доброго."
    assert overlap == 3


def test_stitch_overlap_accepts_small_decoder_variation():
    previous, current, overlap = stitch_overlapping_text(
        "Подскажите сколько будет стоить поездка",
        "сколько будет стоить поездку до аэропорта",
    )

    assert previous == "Подскажите сколько будет стоить поездка"
    assert current == "до аэропорта"
    assert overlap == 4


def test_stitch_does_not_remove_unrelated_single_short_word():
    previous, current, overlap = stitch_overlapping_text("Да.", "Да, начинаем работу.")

    assert previous == "Да."
    assert current == "Да, начинаем работу."
    assert overlap == 0


def test_stitch_aligns_overlap_with_small_insertions_and_substitutions():
    previous, current, overlap = stitch_overlapping_text(
        "Спасибо. Пожалуйста, всего доброго. Три ноля, Ольга.",
        "Пожалуйста, всего доброго. Трина Ольга, здравствуйте.",
    )

    assert previous == "Спасибо. Пожалуйста, всего доброго. Три ноля, Ольга."
    assert current == "здравствуйте."
    assert overlap >= 4


def test_stitch_uses_latest_repeated_overlap_occurrence():
    previous, current, overlap = stitch_overlapping_text(
        (
            "Пожалуйста, всего доброго. Три ноля, Ольга, здравствуйте. "
            "Пожалуйста, всего доброго. Три ноля, Ольга."
        ),
        "Пожалуйста, всего доброго. Трина Ольга, здравствуйте.",
    )

    assert previous.endswith("Три ноля, Ольга.")
    assert current == "здравствуйте."
    assert overlap >= 4


def test_stitch_issue_33_accepts_divergent_words_before_matching_tail():
    previous, current, overlap = stitch_overlapping_text(
        "По ключевым вопросам в мире, были едины.",
        "Во всём мире были едины в отстаивании той или иной точки зрения.",
    )

    assert previous == "По ключевым вопросам в мире, были едины."
    assert current == "в отстаивании той или иной точки зрения."
    assert overlap == 5


def test_stitch_issue_33_accepts_filler_gap_in_previous_tail():
    previous, current, overlap = stitch_overlapping_text(
        "Мы занимались сельским хозяйством, э-э, медициной.",
        "Хозяйством, медициной, образованием, фармакологическим производством.",
    )

    assert previous == "Мы занимались сельским хозяйством, э-э, медициной."
    assert current == "образованием, фармакологическим производством."
    assert overlap == 2


def test_normalize_chunk_words_returns_none_without_usable_timestamps():
    assert chunking.normalize_chunk_words(None, start_sec=10.0, end_sec=20.0) is None
    assert (
        chunking.normalize_chunk_words(
            [{"text": "слово", "start": float("nan"), "end": 11.0}],
            start_sec=10.0,
            end_sec=20.0,
        )
        is None
    )


def test_normalize_chunk_words_trims_and_clips_to_nominal_interval():
    words = chunking.normalize_chunk_words(
        [
            {"text": "повтор", "start": 8.9, "end": 10.2},
            {"text": "новое", "start": 9.8, "end": 10.4},
            {"text": "слово", "start": 10.3, "end": 12.0},
            {"text": "снаружи", "start": 20.1, "end": 20.4},
        ],
        start_sec=10.0,
        end_sec=20.0,
        trim_prefix_words=1,
    )

    assert words == [
        {"text": "новое", "start": 10.0, "end": 10.4},
        {"text": "слово", "start": 10.4, "end": 12.0},
    ]


def test_normalize_chunk_words_drops_zero_duration_after_clipping():
    words = chunking.normalize_chunk_words(
        [
            {"text": "до", "start": 8.0, "end": 9.0},
            {"text": "граница", "start": 9.0, "end": 10.0},
            {"text": "после", "start": 19.9, "end": 21.0},
        ],
        start_sec=10.0,
        end_sec=20.0,
    )

    assert words == [{"text": "после", "start": 19.9, "end": 20.0}]
