"""Reconstruct an overlap-preserving global speaker timeline."""

from __future__ import annotations

import math

import numpy as np

from .base import SpeakerSegment
from .onnx_segmentation import SegmentationResult


def reconstruct_speaker_segments(
    segmentation: SegmentationResult,
    assignments: np.ndarray,
    *,
    onset: float = 0.5,
    offset: float = 0.35,
) -> list[SpeakerSegment]:
    probabilities = segmentation.probabilities
    expected = probabilities.shape[0] * probabilities.shape[2]
    labels = np.asarray(assignments, dtype=np.int64).reshape(-1)
    if len(labels) != expected:
        raise ValueError(f"Ожидалось {expected} local-speaker labels, получено {len(labels)}")
    valid_labels = labels[labels >= 0]
    if len(valid_labels) == 0:
        return []

    speaker_count = int(valid_labels.max()) + 1
    total_frames = max(1, int(math.ceil(segmentation.audio_duration / segmentation.frame_step)))
    sums = np.zeros((total_frames, speaker_count), dtype=np.float32)
    counts = np.zeros((total_frames, speaker_count), dtype=np.int32)
    local_count = probabilities.shape[2]

    for window_index, window_start in enumerate(segmentation.window_starts):
        start_frame = int(round(float(window_start) / segmentation.frame_step))
        for local_speaker in range(local_count):
            label = int(labels[window_index * local_count + local_speaker])
            if label < 0:
                continue
            end_frame = min(total_frames, start_frame + probabilities.shape[1])
            length = end_frame - start_frame
            if length <= 0:
                continue
            sums[start_frame:end_frame, label] += probabilities[window_index, :length, local_speaker]
            counts[start_frame:end_frame, label] += 1

    global_probabilities = np.divide(
        sums,
        counts,
        out=np.zeros_like(sums),
        where=counts > 0,
    )
    segments: list[SpeakerSegment] = []
    for speaker in range(speaker_count):
        active = False
        start_frame = 0
        for frame, probability in enumerate(global_probabilities[:, speaker]):
            if not active and probability >= onset:
                active = True
                start_frame = frame
            elif active and probability < offset:
                active = False
                segments.append(
                    SpeakerSegment(
                        round(start_frame * segmentation.frame_step, 9),
                        round(min(frame * segmentation.frame_step, segmentation.audio_duration), 9),
                        f"SPEAKER_{speaker:02d}",
                    )
                )
        if active:
            segments.append(
                SpeakerSegment(
                    round(start_frame * segmentation.frame_step, 9),
                    round(segmentation.audio_duration, 9),
                    f"SPEAKER_{speaker:02d}",
                )
            )
    segments.sort(key=lambda item: (item.start, item.end, item.speaker))
    return [segment for segment in segments if segment.end > segment.start]
