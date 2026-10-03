"""Speaker labels for live transcripts, without cross-session identity state."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from threading import RLock

from .recorder import join_segments
from .types import CaptureEvent, CaptureEventKind, CaptureSource, TranscriptEvent

LIVE_ESTIMATE_STABILIZATION_HORIZON_SECONDS = 10
LIVE_ESTIMATE_BACKEND = "sortformer"
"""The only backend family designed for streaming speaker estimates."""


class BuiltinDiarizers:
    """Diarization backends shipped with the app, created on request.

    None of them can estimate speakers while recording: pyannote, the ONNX
    chain and Sortformer (NeMo or ONNX) all diarize a finished file and have
    no `estimate_events`. Sessions read `supports_live_estimate` before
    asking for a live estimator, so choosing "Live estimate" no longer loads
    a Sortformer model per source only to report it unavailable.
    """

    supports_live_estimate = False

    def __call__(self, backend: str):
        from src.core.diarization.factory import create_diarization_backend

        return create_diarization_backend(backend)


class SpeakerLabeler:
    """Name the speakers of finished events, live or after stop.

    Raw backend labels are replaced by "Speaker N" numbered in order of
    appearance per session; a revision carries the new label as a newer
    final. Live estimates run on the ASR thread of their source without the
    session lock (creating a diarizer loads a model); `lock` is the session's
    and is taken only to report.
    """

    def __init__(
        self,
        factory: Callable[[str], object],
        *,
        asr_sample_rate: int,
        after_stop_backend: str,
        translate: Callable[[str, str], str],
        notify: Callable[[CaptureEvent], None],
        lock: RLock,
    ) -> None:
        self._factory = factory
        self._asr_sample_rate = asr_sample_rate
        self._after_stop_backend = after_stop_backend
        self._translate = translate
        self._notify = notify
        self._lock = lock
        self._live_diarizers: dict[CaptureSource, object] = {}
        self._live_unavailable: set[CaptureSource] = set()
        self._labels: dict[tuple[CaptureSource, str], str] = {}

    def estimate_live(self, source: CaptureSource, recent: list[TranscriptEvent]) -> dict:
        """Runs without the session lock; only the ASR thread of `source` calls it."""
        if source in self._live_unavailable:
            return {}
        if not getattr(self._factory, "supports_live_estimate", True):
            with self._lock:
                self.report_live_unavailable(
                    source,
                    self._translate(
                        "Оценка спикеров во время записи недоступна с установленными движками диаризации.",
                        "Live speaker estimates are not available with the installed diarization backends.",
                    ),
                )
            return {}
        diarizer = self._live_diarizers.get(source)
        if diarizer is None:
            try:
                diarizer = self._factory(LIVE_ESTIMATE_BACKEND)
                self._live_diarizers[source] = diarizer
            except Exception as exc:
                with self._lock:
                    self.report_live_unavailable(source, str(exc))
                return {}
        estimate = getattr(diarizer, "estimate_events", None)
        if not callable(estimate):
            with self._lock:
                self.report_live_unavailable(
                    source,
                    "Live Sortformer estimate unavailable; use After stop for offline speaker labels. "
                    "Retaining source labels.",
                )
            return {}
        try:
            return estimate(
                recent,
                stabilization_horizon_seconds=LIVE_ESTIMATE_STABILIZATION_HORIZON_SECONDS,
            )
        except Exception as exc:
            with self._lock:
                self.report_live_unavailable(source, str(exc))
            return {}

    def diarize_recordings(
        self,
        recordings: Mapping[CaptureSource, Path],
        files: Mapping[str, list[Path]],
        *,
        events_for: Callable[[CaptureSource], list[TranscriptEvent]],
        publish: Callable[[TranscriptEvent], None],
        work_dir: Path,
    ) -> None:
        diarizer = None
        for source, path in recordings.items():
            parts = list(files.get(source.value) or [path])
            joined = None
            try:
                # One model for every source: it used to be loaded per source.
                if diarizer is None:
                    diarizer = self._factory(self._after_stop_backend)
                # A rolled-over track is diarized as one file: the first
                # segment alone left everything after ~15 min unlabelled.
                if len(parts) > 1:
                    joined = join_segments(parts, work_dir / f".{source.value}-diarize.flac")
                segments = diarizer.diarize(str(joined or parts[0]))
                events = events_for(source)
                for revised in self.revised(events, self.segment_speakers(events, segments)):
                    publish(revised)
            except Exception as exc:
                self._notify(CaptureEvent(
                    CaptureEventKind.STATUS,
                    source,
                    0,
                    0,
                    f"After-stop diarization unavailable: {exc}. Retaining source labels.",
                ))
            finally:
                if joined is not None:
                    joined.unlink(missing_ok=True)

    def segment_speakers(self, events, segments) -> dict[str, str]:
        speakers = {}
        for event in events:
            start = event.sample_start / self._asr_sample_rate
            end = event.sample_end / self._asr_sample_rate
            # Only segments that actually overlap: with all overlaps at zero,
            # max() fell back to comparing labels and named a speaker who was
            # not talking.
            overlaps = [
                (overlap, segment.speaker)
                for segment in segments
                if (overlap := min(end, segment.end) - max(start, segment.start)) > 0
            ]
            if overlaps:
                _, speaker = max(overlaps, key=lambda item: item[0])
                speakers[event.event_id] = speaker
        return speakers

    def revised(self, events, estimates) -> list[TranscriptEvent]:
        revised = []
        for event in events:
            speaker = estimates.get(event.event_id)
            if speaker is None:
                continue
            anonymous = self.anonymous(event.source, str(speaker))
            if event.speaker != anonymous:
                revised.append(replace(event, revision=event.revision + 1, speaker=anonymous, supersedes=event.revision))
        return revised

    def anonymous(self, source: CaptureSource, raw_speaker: str) -> str:
        key = (source, raw_speaker)
        number = len(self._labels) + 1
        return self._labels.setdefault(
            key,
            self._translate(f"Спикер {number}", f"Speaker {number}"),
        )

    def report_live_unavailable(self, source: CaptureSource, detail: str) -> None:
        if source in self._live_unavailable:
            return
        self._live_unavailable.add(source)
        guidance = self._translate(
            "Используйте «После остановки» для офлайн-меток спикеров; "
            "метки источников сохраняются.",
            "Use After stop for offline speaker labels; retaining source labels.",
        )
        self._notify(CaptureEvent(
            CaptureEventKind.STATUS,
            source,
            0,
            0,
            f"{detail} {guidance}",
        ))
