"""Pseudo-streaming ASR work scheduling independent of capture and Qt."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from src.core.asr.types import TranscriptionSegment

from .types import CaptureSource, PcmChunk, TranscriptEvent


class WindowBackend(Protocol):
    def transcribe_window(
        self,
        audio: np.ndarray,
        sample_rate: int,
        offset_samples: int,
    ) -> list[TranscriptionSegment]: ...


@dataclass
class _SpeechRun:
    start: int
    end: int
    audio: list[np.ndarray]
    silence_start: int | None = None
    silence_samples: int = 0
    last_partial_end: int | None = None
    voiced_samples: int = 0


@dataclass(frozen=True)
class _Job:
    source: CaptureSource
    start: int
    end: int
    event_start: int
    audio: np.ndarray
    is_final: bool
    paragraph_break_after: bool
    voiced_samples: int


@dataclass
class _Hypothesis:
    committed: list[str]
    words: list[str]


class _EnergyGate:
    """Adapt to the room floor without treating low-level noise as speech."""

    def __init__(self) -> None:
        self._noise_floor = 0.001
        self._active = False

    def is_voiced(self, audio: np.ndarray) -> bool:
        rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
        attack = max(0.015, self._noise_floor * 4)
        release = max(0.010, self._noise_floor * 2.5)
        self._active = rms >= (release if self._active else attack)
        if not self._active:
            self._noise_floor = self._noise_floor * 0.95 + rms * 0.05
        return self._active


class LiveAsrScheduler:
    """Prioritize committed speech and retain only the newest partial decode."""

    def __init__(
        self,
        backend: WindowBackend,
        *,
        partial_delay_seconds: float = 1.5,
        partial_context_seconds: float = 12.0,
        partial_minimum_seconds: float = 1.5,
        final_silence_seconds: float = 3.0,
        on_partial: Callable[[TranscriptEvent], None] | None = None,
        on_final: Callable[[TranscriptEvent], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        if min(
            partial_delay_seconds, partial_context_seconds, partial_minimum_seconds, final_silence_seconds,
        ) <= 0:
            raise ValueError("live ASR timing values must be positive")
        if partial_minimum_seconds > partial_context_seconds:
            raise ValueError("partial minimum cannot exceed partial context")
        self._backend = backend
        self._partial_delay_seconds = partial_delay_seconds
        self._partial_context_seconds = partial_context_seconds
        self._partial_minimum_seconds = partial_minimum_seconds
        self._final_silence_seconds = final_silence_seconds
        self._on_partial = on_partial
        self._on_final = on_final
        self._on_error = on_error
        self._runs: dict[CaptureSource, _SpeechRun] = {}
        self._energy_gates: dict[CaptureSource, _EnergyGate] = {}
        self._final_jobs: deque[_Job] = deque()
        self._partial_job: _Job | None = None
        self._partial_revisions: dict[str, int] = {}
        self._partial_hypotheses: dict[str, _Hypothesis] = {}
        self._refresh_seconds = partial_delay_seconds
        self._closed = False
        self._condition = threading.Condition()
        self._worker = threading.Thread(target=self._run, name="live-asr", daemon=True)
        self._worker.start()

    @property
    def pending_partial_offset(self) -> int | None:
        with self._condition:
            return None if self._partial_job is None else self._partial_job.end

    @property
    def refresh_seconds(self) -> float:
        with self._condition:
            return self._refresh_seconds

    def submit(self, chunk: PcmChunk) -> None:
        if chunk.sample_rate != 16_000 or chunk.channels != 1:
            raise ValueError("live ASR requires derived 16 kHz mono chunks")
        audio = chunk.frames[:, 0]
        voiced = self._energy_gates.setdefault(chunk.source, _EnergyGate()).is_voiced(audio)
        with self._condition:
            if self._closed:
                raise RuntimeError("scheduler is closed")
            run = self._runs.get(chunk.source)
            if voiced:
                if run is None:
                    run = _SpeechRun(chunk.sample_offset, chunk.sample_offset, [])
                    self._runs[chunk.source] = run
                run.audio.append(audio.copy())
                run.end = chunk.sample_offset + len(audio)
                run.voiced_samples += len(audio)
                run.silence_start = None
                run.silence_samples = 0
                if self._should_refresh_partial(run, chunk.sample_rate):
                    self._partial_job = self._job(chunk.source, run, is_final=False)
                    run.last_partial_end = run.end
                    self._condition.notify()
            elif run is not None:
                run.audio.append(audio.copy())
                run.end = chunk.sample_offset + len(audio)
                if run.silence_start is None:
                    run.silence_start = chunk.sample_offset
                run.silence_samples += len(audio)
                if run.silence_samples >= self._final_silence_seconds * chunk.sample_rate:
                    self._final_jobs.append(
                        self._job(chunk.source, run, is_final=True, paragraph_break_after=True)
                    )
                    del self._runs[chunk.source]
                    self._condition.notify()

    def flush(self) -> None:
        with self._condition:
            for source, run in list(self._runs.items()):
                self._final_jobs.append(self._job(source, run, is_final=True))
                del self._runs[source]
            self._condition.notify_all()

    def record_decode_duration(self, seconds: float) -> None:
        with self._condition:
            self._refresh_seconds = min(1.5, max(0.25, seconds))

    def close(self, timeout: float | None = None) -> None:
        """Drain queued decodes; `timeout` bounds the wait, `None` waits fully.

        Abandoning the queue here used to drop every final that had not been
        decoded within a second, which silently emptied short sessions.
        """
        self.flush()
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        self._worker.join(timeout=timeout)

    @property
    def pending_jobs(self) -> int:
        with self._condition:
            return len(self._final_jobs) + (0 if self._partial_job is None else 1)

    def _should_refresh_partial(self, run: _SpeechRun, sample_rate: int) -> bool:
        if run.end - run.start < self._partial_minimum_seconds * sample_rate:
            return False
        return (
            run.last_partial_end is None
            or run.end - run.last_partial_end >= self._refresh_seconds * sample_rate
        )

    def _job(
        self,
        source: CaptureSource,
        run: _SpeechRun,
        *,
        is_final: bool,
        paragraph_break_after: bool = False,
    ) -> _Job:
        audio = np.concatenate(run.audio)
        start = run.start
        if not is_final:
            maximum = round(self._partial_context_seconds * 16_000)
            if len(audio) > maximum:
                audio = audio[-maximum:]
                start = run.end - len(audio)
        return _Job(
            source,
            start,
            run.end,
            run.start,
            audio,
            is_final,
            paragraph_break_after,
            run.voiced_samples,
        )

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._final_jobs and self._partial_job is None and not self._closed:
                    self._condition.wait()
                if self._final_jobs:
                    job = self._final_jobs.popleft()
                elif self._partial_job is not None:
                    job = self._partial_job
                    self._partial_job = None
                elif self._closed:
                    return
                else:
                    continue
            started = time.monotonic()
            try:
                segments = self._backend.transcribe_window(job.audio, 16_000, job.start)
                self._publish(job, segments)
            except Exception as exc:
                if self._on_error is not None:
                    self._on_error(exc)
            finally:
                self.record_decode_duration(time.monotonic() - started)

    def _publish(self, job: _Job, segments: list[TranscriptionSegment]) -> None:
        if not segments:
            return
        text = " ".join(segment["transcription"].strip() for segment in segments).strip()
        if not text:
            return
        if not self._has_acceptable_confidence(segments):
            return
        # A phrase shorter than the partial cadence never schedules a partial,
        # so requiring one here dropped every brief utterance outright.
        if job.is_final and (job.voiced_samples < 16_000 or len(text.split()) < 2):
            return
        event_id = f"{job.source.value}-{job.event_start}"
        if not job.is_final:
            text = self._reconcile_partial(event_id, text)
        else:
            self._partial_hypotheses.pop(event_id, None)
        revision = self._partial_revisions.get(event_id, -1) + 1
        self._partial_revisions[event_id] = revision
        event = TranscriptEvent(
            event_id=event_id,
            revision=revision,
            source=job.source,
            sample_start=job.event_start,
            sample_end=job.end,
            timestamp_ns=time.time_ns(),
            text=text,
            status="final" if job.is_final else "partial",
            supersedes=revision - 1 if revision else None,
            paragraph_break_after=job.paragraph_break_after,
        )
        callback = self._on_final if job.is_final else self._on_partial
        if callback is not None:
            callback(event)

    def _reconcile_partial(self, event_id: str, text: str) -> str:
        incoming = text.split()
        hypothesis = self._partial_hypotheses.get(event_id)
        if hypothesis is None:
            self._partial_hypotheses[event_id] = _Hypothesis([], incoming)
            return text

        common = self._common_prefix_length(hypothesis.words, incoming)
        if common >= len(hypothesis.committed):
            committed = hypothesis.words[:common]
            words = committed + incoming[common:]
        else:
            overlap = self._suffix_prefix_overlap(hypothesis.words, incoming)
            if overlap:
                committed = hypothesis.words[:-overlap]
                words = committed + incoming
            elif self._is_repeated_regression(incoming[common:]):
                return " ".join(hypothesis.words)
            else:
                committed = hypothesis.committed
                words = committed + incoming[common:]
        self._partial_hypotheses[event_id] = _Hypothesis(committed, words)
        return " ".join(words)

    @staticmethod
    def _common_prefix_length(left: list[str], right: list[str]) -> int:
        length = 0
        for previous, current in zip(left, right, strict=False):
            if previous.casefold() != current.casefold():
                break
            length += 1
        return length

    @staticmethod
    def _suffix_prefix_overlap(previous: list[str], current: list[str]) -> int:
        for length in range(min(len(previous), len(current)), 0, -1):
            if [word.casefold() for word in previous[-length:]] == [
                word.casefold() for word in current[:length]
            ]:
                return length
        return 0

    @staticmethod
    def _is_repeated_regression(words: list[str]) -> bool:
        normalized = [word.casefold().strip(".,!?;:-") for word in words]
        return len(normalized) >= 2 and len(set(normalized)) == 1

    @staticmethod
    def _has_acceptable_confidence(segments: list[TranscriptionSegment]) -> bool:
        confidences = [
            confidence for segment in segments
            if isinstance((confidence := segment.get("confidence")), (int, float))
        ]
        return not confidences or min(confidences) >= 0.45
