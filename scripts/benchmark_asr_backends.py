#!/usr/bin/env python3
"""Local-manifest ASR quality/performance benchmark for release gates."""

from __future__ import annotations

import argparse
import json
import re
import resource
import time
from pathlib import Path


def normalize_text(text: str) -> str:
    normalized = str(text).lower().replace("ё", "е")
    normalized = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def _edit_distance(reference: list[str], hypothesis: list[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for row, expected in enumerate(reference, 1):
        current = [row]
        for column, actual in enumerate(hypothesis, 1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (expected != actual),
            ))
        previous = current
    return previous[-1]


def error_rate(reference: str, hypothesis: str, *, unit: str) -> float:
    expected = normalize_text(reference)
    actual = normalize_text(hypothesis)
    if unit == "word":
        expected_units, actual_units = expected.split(), actual.split()
    elif unit == "char":
        expected_units, actual_units = list(expected.replace(" ", "")), list(actual.replace(" ", ""))
    else:
        raise ValueError("unit must be 'word' or 'char'")
    return _edit_distance(expected_units, actual_units) / max(1, len(expected_units))


def find_boundary_duplicates(segments: list[dict], *, max_tokens: int = 8) -> list[dict]:
    duplicates = []
    for index, (left, right) in enumerate(zip(segments, segments[1:], strict=False)):
        left_tokens = normalize_text(left.get("transcription", "")).split()
        right_tokens = normalize_text(right.get("transcription", "")).split()
        matched = []
        for size in range(1, min(max_tokens, len(left_tokens), len(right_tokens)) + 1):
            if left_tokens[-size:] == right_tokens[:size]:
                matched = left_tokens[-size:]
        if matched:
            duplicates.append({"left": index, "right": index + 1, "tokens": matched})
    return duplicates


def quality_record(**values) -> dict:
    audio_seconds = float(values["audio_seconds"])
    elapsed = float(values["elapsed_seconds"])
    return {
        "backend": values["backend"],
        "model": values["model"],
        "provider": values["provider"],
        "wer": error_rate(values["reference"], values["hypothesis"], unit="word"),
        "cer": error_rate(values["reference"], values["hypothesis"], unit="char"),
        "elapsed_seconds": elapsed,
        "audio_seconds": audio_seconds,
        "rtfx": audio_seconds / elapsed if elapsed > 0 else None,
        "peak_rss_bytes": int(values["peak_rss_bytes"]),
        "boundary_duplicates": find_boundary_duplicates(values["segments"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--backend", action="append", required=True)
    parser.add_argument("--model", default="v3_e2e_rnnt")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    entries = json.loads(args.manifest.read_text(encoding="utf-8"))
    records = []
    import soundfile as sf

    from src.core.model_loader import ModelLoader

    for backend in args.backend:
        loader = ModelLoader(requested_backend=backend, model_revision=args.model)
        if not loader.load_model():
            raise RuntimeError(f"Не удалось загрузить {backend}")
        for entry in entries:
            started = time.perf_counter()
            segments = loader.transcribe_longform(entry["audio"])
            elapsed = time.perf_counter() - started
            info = sf.info(entry["audio"])
            hypothesis = " ".join(segment["transcription"] for segment in segments)
            diagnostics = loader.diagnostics()
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
            records.append(quality_record(
                backend=backend, model=args.model, provider=diagnostics.get("provider"),
                reference=entry["reference"], hypothesis=hypothesis,
                elapsed_seconds=elapsed, audio_seconds=info.duration,
                peak_rss_bytes=peak, segments=segments,
            ))
        loader.unload()
    args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
