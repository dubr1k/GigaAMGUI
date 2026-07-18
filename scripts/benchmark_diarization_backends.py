#!/usr/bin/env python3
"""RTTM-based local benchmark for ONNX, pyannote, and Sortformer diarization."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment


@dataclass(frozen=True)
class SpeakerTurn:
    start: float
    end: float
    speaker: str


def parse_rttm(text: str) -> list[SpeakerTurn]:
    turns = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 8 or parts[0] != "SPEAKER":
            raise ValueError(f"Некорректная RTTM-строка: {line}")
        start, duration = float(parts[3]), float(parts[4])
        if start < 0 or duration <= 0:
            raise ValueError(f"Некорректный RTTM-интервал: {line}")
        turns.append(SpeakerTurn(start, start + duration, parts[7]))
    return sorted(turns, key=lambda turn: (turn.start, turn.end, turn.speaker))


def _activity(turns: list[SpeakerTurn], frames: int, step: float):
    speakers = sorted({turn.speaker for turn in turns})
    activity = np.zeros((frames, len(speakers)), dtype=np.bool_)
    index = {speaker: column for column, speaker in enumerate(speakers)}
    for turn in turns:
        start = max(0, int(np.floor(turn.start / step)))
        end = min(frames, int(np.ceil(turn.end / step)))
        activity[start:end, index[turn.speaker]] = True
    return speakers, activity


def diarization_metrics(reference, hypothesis, *, frame_seconds: float = 0.01) -> dict[str, float]:
    duration = max([turn.end for turn in [*reference, *hypothesis]], default=0.0)
    frames = max(1, int(np.ceil(duration / frame_seconds)))
    ref_speakers, ref = _activity(reference, frames, frame_seconds)
    hyp_speakers, hyp = _activity(hypothesis, frames, frame_seconds)
    overlap = ref.astype(np.int64).T @ hyp.astype(np.int64)
    mapping = {}
    if overlap.size:
        rows, columns = linear_sum_assignment(-overlap)
        mapping = {int(column): int(row) for row, column in zip(rows, columns, strict=True)}
    aligned = np.zeros_like(ref)
    unmatched_hyp = 0
    for hyp_column in range(len(hyp_speakers)):
        if hyp_column in mapping:
            aligned[:, mapping[hyp_column]] |= hyp[:, hyp_column]
        else:
            unmatched_hyp += int(hyp[:, hyp_column].sum())
    errors = int(np.logical_xor(ref, aligned).sum()) + unmatched_hyp
    denominator = max(1, int(ref.sum()))
    jer_values = []
    for column in range(len(ref_speakers)):
        intersection = int(np.logical_and(ref[:, column], aligned[:, column]).sum())
        union = int(np.logical_or(ref[:, column], aligned[:, column]).sum())
        jer_values.append(1.0 - intersection / max(1, union))
    return {
        "der": errors / denominator,
        "jer": float(np.mean(jer_values)) if jer_values else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--backend", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    entries = json.loads(args.manifest.read_text(encoding="utf-8"))
    from src.core.diarization.factory import create_diarization_backend

    records = []
    for backend_name in args.backend:
        backend = create_diarization_backend(backend_name)
        for entry in entries:
            reference = parse_rttm(Path(entry["rttm"]).read_text(encoding="utf-8"))
            predicted = backend.diarize(entry["audio"], num_speakers=entry.get("num_speakers"))
            hypothesis = [SpeakerTurn(item.start, item.end, item.speaker) for item in predicted]
            records.append({"backend": backend_name, "audio": entry["audio"], **diarization_metrics(reference, hypothesis)})
        backend.unload()
    args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
