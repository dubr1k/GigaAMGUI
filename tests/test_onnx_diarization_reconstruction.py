import numpy as np

from src.core.diarization.onnx_segmentation import SegmentationResult
from src.core.diarization.reconstruction import reconstruct_speaker_segments


def _seg(probabilities, starts, *, step=1.0, duration=4.0):
    return SegmentationResult(
        probabilities=np.asarray(probabilities, dtype=np.float32),
        window_starts=np.asarray(starts, dtype=np.float64),
        frame_step=step,
        audio_duration=duration,
    )


def test_window_local_speaker_permutation_maps_to_global_speakers():
    segmentation = _seg(
        [
            [[0.9, 0.1, 0.0], [0.9, 0.1, 0.0]],
            [[0.1, 0.9, 0.0], [0.1, 0.9, 0.0]],
        ],
        [0.0, 2.0],
    )
    # Second window local speaker 1 is the same global speaker as first local 0.
    assignments = np.asarray([0, 1, -1, 1, 0, -1])

    segments = reconstruct_speaker_segments(segmentation, assignments)

    speaker0 = [segment for segment in segments if segment.speaker == "SPEAKER_00"]
    assert [(segment.start, segment.end) for segment in speaker0] == [(0.0, 4.0)]


def test_overlap_is_preserved_as_two_simultaneous_segments():
    segmentation = _seg([[[0.9, 0.8, 0.0], [0.9, 0.8, 0.0]]], [0.0], duration=2.0)

    segments = reconstruct_speaker_segments(segmentation, np.asarray([0, 1, -1]))

    assert [(s.start, s.end, s.speaker) for s in segments] == [
        (0.0, 2.0, "SPEAKER_00"),
        (0.0, 2.0, "SPEAKER_01"),
    ]


def test_hysteresis_bridges_short_probability_dip():
    segmentation = _seg([[[0.8, 0.0, 0.0], [0.4, 0.0, 0.0], [0.8, 0.0, 0.0]]], [0.0], duration=3.0)

    segments = reconstruct_speaker_segments(
        segmentation,
        np.asarray([0, -1, -1]),
        onset=0.5,
        offset=0.3,
    )

    assert [(s.start, s.end) for s in segments] == [(0.0, 3.0)]


def test_forced_single_speaker_uses_max_within_window_instead_of_diluting_tracks():
    """num_speakers=1 may merge permuted local tracks from the same window.

    Averaging all three tracks divided each 0.9 activation by three and erased
    the entire utterance below the 0.5 onset threshold.
    """
    segmentation = _seg(
        [[
            [0.9, 0.0, 0.0],
            [0.9, 0.0, 0.0],
            [0.0, 0.9, 0.0],
            [0.0, 0.9, 0.0],
        ]],
        [0.0],
        duration=4.0,
    )

    segments = reconstruct_speaker_segments(
        segmentation,
        np.asarray([0, 0, 0]),
    )

    assert [(s.start, s.end, s.speaker) for s in segments] == [
        (0.0, 4.0, "SPEAKER_00"),
    ]
