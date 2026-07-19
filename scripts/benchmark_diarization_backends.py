#!/usr/bin/env python3
"""RTTM-based local benchmark for ONNX, pyannote, and Sortformer diarization."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from functools import partial
from pathlib import Path


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


def format_rttm(turns: list[SpeakerTurn], *, uri: str) -> str:
    """Сериализовать гипотезу так, чтобы её принимал parse_rttm."""
    return "".join(
        f"SPEAKER {uri} 1 {turn.start:.3f} {turn.end - turn.start:.3f} "
        f"<NA> <NA> {turn.speaker.replace(' ', '_')} <NA> <NA>\n"
        for turn in turns
        if turn.end > turn.start
    )


def _annotation(turns: list[SpeakerTurn]):
    from pyannote.core import Annotation, Segment

    annotation = Annotation()
    for index, turn in enumerate(turns):
        if turn.end > turn.start:
            annotation[Segment(turn.start, turn.end), index] = turn.speaker
    return annotation


def diarization_metrics(
    reference: list[SpeakerTurn],
    hypothesis: list[SpeakerTurn],
    *,
    collar: float = 0.0,
    skip_overlap: bool = False,
) -> dict[str, float]:
    """DER с разбивкой на составляющие, JER и расхождение по числу спикеров.

    Считает pyannote.metrics, а не собственная растеризация: без разбивки на
    miss / false alarm / confusion непонятно, режет ли кластеризация одного
    спикера на несколько или, наоборот, склеивает разных, — а вопрос стоит
    именно так.
    """
    from pyannote.metrics.diarization import DiarizationErrorRate, JaccardErrorRate

    reference_annotation = _annotation(reference)
    hypothesis_annotation = _annotation(hypothesis)

    components = DiarizationErrorRate(collar=collar, skip_overlap=skip_overlap)(
        reference_annotation,
        hypothesis_annotation,
        detailed=True,
    )
    total = float(components["total"]) or 1.0
    jer = float(
        JaccardErrorRate(collar=collar, skip_overlap=skip_overlap)(
            reference_annotation,
            hypothesis_annotation,
        )
    )

    reference_speakers = len(reference_annotation.labels())
    hypothesis_speakers = len(hypothesis_annotation.labels())
    return {
        "der": float(components["diarization error rate"]),
        "miss": float(components["missed detection"]) / total,
        "false_alarm": float(components["false alarm"]) / total,
        "confusion": float(components["confusion"]) / total,
        "jer": jer,
        "reference_speakers": reference_speakers,
        "hypothesis_speakers": hypothesis_speakers,
        "speaker_count_error": hypothesis_speakers - reference_speakers,
    }


def build_backend(backend_name: str, threshold: float | None):
    """Собрать backend, при необходимости подменив порог кластеризации.

    Порог намеренно не выведен в production-конфиг: подбирать его можно только
    по корпусу с DER, а не по одному файлу. Для свипа хватает штатного шва
    cluster_fn.
    """
    from src.core.diarization.factory import create_diarization_backend

    if threshold is None:
        return create_diarization_backend(backend_name)
    if backend_name != "onnx":
        raise ValueError("--threshold применим только к backend onnx")

    from src.core.diarization.clustering import cluster_embeddings
    from src.core.diarization.onnx_backend import OnnxDiarizationBackend

    return create_diarization_backend(
        backend_name,
        onnx_factory=lambda **kwargs: OnnxDiarizationBackend(
            **kwargs,
            cluster_fn=partial(cluster_embeddings, threshold=threshold),
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--backend", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--threshold",
        action="append",
        type=float,
        help="Порог кластеризации ONNX; можно повторить для свипа",
    )
    parser.add_argument(
        "--collar",
        type=float,
        default=0.0,
        help="Прощаемая окрестность границ реплики, с (в DIHARD принято 0.25)",
    )
    parser.add_argument("--skip-overlap", action="store_true")
    parser.add_argument(
        "--dump-rttm",
        type=Path,
        help="Куда сложить гипотезы в RTTM. Записи без ключа rttm обрабатываются "
        "только так — это способ собрать эталон доверенным backend-ом",
    )
    parser.add_argument(
        "--ignore-manifest-speakers",
        action="store_true",
        help="Не передавать num_speakers из манифеста: проверка авто-режима",
    )
    args = parser.parse_args()

    # pyannote и Sortformer читают HF_TOKEN из .env — как и всё остальное
    # приложение, но фабрика диаризации сама конфиг не подтягивает.
    import src.config  # noqa: F401, PLC0415

    entries = json.loads(args.manifest.read_text(encoding="utf-8"))
    if args.dump_rttm:
        args.dump_rttm.mkdir(parents=True, exist_ok=True)

    records = []
    for backend_name in args.backend:
        for threshold in args.threshold or [None]:
            backend = build_backend(backend_name, threshold)
            label = backend_name if threshold is None else f"{backend_name}@{threshold}"
            for entry in entries:
                audio = entry["audio"]
                num_speakers = None if args.ignore_manifest_speakers else entry.get("num_speakers")

                began = time.perf_counter()
                predicted = backend.diarize(audio, num_speakers=num_speakers)
                elapsed = time.perf_counter() - began

                hypothesis = [
                    SpeakerTurn(item.start, item.end, item.speaker) for item in predicted
                ]
                if args.dump_rttm:
                    stem = Path(audio).stem
                    (args.dump_rttm / f"{stem}.{label}.rttm").write_text(
                        format_rttm(hypothesis, uri=stem),
                        encoding="utf-8",
                    )

                record: dict = {
                    "backend": backend_name,
                    "audio": audio,
                    "elapsed_seconds": round(elapsed, 3),
                }
                if threshold is not None:
                    record["threshold"] = threshold

                if entry.get("rttm"):
                    reference = parse_rttm(Path(entry["rttm"]).read_text(encoding="utf-8"))
                    record.update(
                        diarization_metrics(
                            reference,
                            hypothesis,
                            collar=args.collar,
                            skip_overlap=args.skip_overlap,
                        )
                    )
                elif args.dump_rttm:
                    record["hypothesis_speakers"] = len({turn.speaker for turn in hypothesis})
                else:
                    raise ValueError(
                        f"Запись без ключа rttm обрабатывается только с --dump-rttm: {audio}"
                    )

                records.append(record)
                print(json.dumps(record, ensure_ascii=False))
            backend.unload()

    args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
