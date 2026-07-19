from scripts.benchmark_diarization_backends import (
    SpeakerTurn,
    diarization_metrics,
    format_rttm,
    parse_rttm,
)


def test_parse_rttm_preserves_overlapping_turns():
    turns = parse_rttm(
        "SPEAKER file 1 0.0 2.0 <NA> <NA> A <NA> <NA>\n"
        "SPEAKER file 1 1.0 2.0 <NA> <NA> B <NA> <NA>\n"
    )
    assert turns == [SpeakerTurn(0.0, 2.0, "A"), SpeakerTurn(1.0, 3.0, "B")]


def test_diarization_metrics_are_permutation_invariant():
    reference = [SpeakerTurn(0, 1, "A"), SpeakerTurn(1, 2, "B")]
    hypothesis = [SpeakerTurn(0, 1, "Y"), SpeakerTurn(1, 2, "X")]
    metrics = diarization_metrics(reference, hypothesis)
    assert metrics["der"] == 0.0
    assert metrics["jer"] == 0.0
    assert metrics["speaker_count_error"] == 0


def test_diarization_metrics_penalize_missed_overlap():
    reference = [SpeakerTurn(0, 2, "A"), SpeakerTurn(1, 2, "B")]
    hypothesis = [SpeakerTurn(0, 2, "X")]
    metrics = diarization_metrics(reference, hypothesis)
    assert metrics["der"] > 0.0
    assert metrics["jer"] > 0.0
    # Пропущенная речь — это miss, а не confusion: разбивка должна это показывать.
    assert metrics["miss"] > 0.0
    assert metrics["confusion"] == 0.0
    assert metrics["speaker_count_error"] == -1


def test_splitting_one_speaker_in_two_is_confusion_not_miss():
    """Ровно тот дефект, который ищем в авто-режиме: лишние кластеры."""
    reference = [SpeakerTurn(0, 4, "A")]
    hypothesis = [SpeakerTurn(0, 2, "X"), SpeakerTurn(2, 4, "Y")]
    metrics = diarization_metrics(reference, hypothesis)

    assert metrics["speaker_count_error"] == 1
    assert metrics["confusion"] > 0.0
    assert metrics["miss"] == 0.0
    assert metrics["false_alarm"] == 0.0


def test_collar_forgives_small_boundary_shift():
    reference = [SpeakerTurn(0, 2, "A"), SpeakerTurn(2, 4, "B")]
    hypothesis = [SpeakerTurn(0, 2.1, "X"), SpeakerTurn(2.1, 4, "Y")]

    assert diarization_metrics(reference, hypothesis)["der"] > 0.0
    assert diarization_metrics(reference, hypothesis, collar=0.5)["der"] == 0.0


def test_dumped_rttm_round_trips_through_parser():
    turns = [SpeakerTurn(0.0, 1.5, "Спикер №1"), SpeakerTurn(1.5, 3.0, "Спикер №2")]

    restored = parse_rttm(format_rttm(turns, uri="запись"))

    assert [(turn.start, turn.end) for turn in restored] == [(0.0, 1.5), (1.5, 3.0)]
    # Пробел в метке разорвал бы RTTM-строку на лишние поля.
    assert [turn.speaker for turn in restored] == ["Спикер_№1", "Спикер_№2"]
