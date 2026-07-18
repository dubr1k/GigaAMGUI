from scripts.benchmark_diarization_backends import (
    SpeakerTurn,
    diarization_metrics,
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
    metrics = diarization_metrics(reference, hypothesis, frame_seconds=0.1)
    assert metrics["der"] == 0.0
    assert metrics["jer"] == 0.0


def test_diarization_metrics_penalize_missed_overlap():
    reference = [SpeakerTurn(0, 2, "A"), SpeakerTurn(1, 2, "B")]
    hypothesis = [SpeakerTurn(0, 2, "X")]
    metrics = diarization_metrics(reference, hypothesis, frame_seconds=0.1)
    assert metrics["der"] > 0.0
    assert metrics["jer"] > 0.0
