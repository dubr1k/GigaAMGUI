"""Best-effort per-source recording: failures are counted, reported once, then given up on."""

from __future__ import annotations

from collections.abc import Callable

from .reporting import ReportOnce
from .types import CaptureEvent, CaptureEventKind, CaptureSource, PcmChunk

MAX_RECORDING_FAILURES = 5
"""Consecutive write failures before a source's recording is given up on.

A writer that cannot open its file does not start working later, and retrying
per chunk turns one fault into thousands of identical log lines — issue #48
produced 15 255 of them in a single session."""


class RecordingGuard:
    """Write source audio without ever letting the recording cost recognition.

    Losing the FLAC track must not cost the user the transcript they came
    for, so a failed write is logged, reported once per distinct reason, and
    after `MAX_RECORDING_FAILURES` consecutive failures the source's track is
    disabled for the session. A successful write resets the count: a
    transient disk hiccup must not kill the track.
    """

    def __init__(
        self,
        write: Callable[[PcmChunk], None],
        *,
        notify: Callable[[CaptureEvent], None],
        log: Callable[[str], None],
        translate: Callable[[str, str], str],
    ) -> None:
        self._write = write
        self._notify = notify
        self._log = log
        self._translate = translate
        self._reported = ReportOnce()
        self._failures: dict[CaptureSource, int] = {}
        self._disabled: set[CaptureSource] = set()

    def write(self, chunk: PcmChunk) -> None:
        if chunk.source in self._disabled:
            return
        try:
            self._write(chunk)
        except Exception as exc:
            self._report_failure(chunk, exc)
        else:
            self._failures.pop(chunk.source, None)

    def _report_failure(self, chunk: PcmChunk, exc: Exception) -> None:
        detail = f"{type(exc).__name__}: {exc}"
        self._log(f"recording write failed [{chunk.source.value}]: {detail}")
        failures = self._failures.get(chunk.source, 0) + 1
        self._failures[chunk.source] = failures
        if failures >= MAX_RECORDING_FAILURES:
            self._disable(chunk, detail)
            return
        if not self._reported.first(detail):
            return
        self._notify(CaptureEvent(
            CaptureEventKind.STATUS,
            chunk.source,
            chunk.sample_offset,
            chunk.timestamp_ns,
            self._translate(
                f"Запись аудио источника прервана: {detail}. Распознавание продолжается.",
                f"Source audio recording failed: {detail}. Recognition continues.",
            ),
        ))

    def _disable(self, chunk: PcmChunk, reason: str) -> None:
        if chunk.source in self._disabled:
            return
        self._disabled.add(chunk.source)
        self._log(
            f"source recording disabled [{chunk.source.value}] after "
            f"{MAX_RECORDING_FAILURES} consecutive failures: {reason}"
        )
        self._notify(CaptureEvent(
            CaptureEventKind.STATUS,
            chunk.source,
            chunk.sample_offset,
            chunk.timestamp_ns,
            self._translate(
                f"Запись аудио источника отключена для этой сессии: {reason}. "
                "Распознавание продолжается.",
                f"Source audio recording disabled for this session: {reason}. "
                "Recognition continues.",
            ),
        ))
