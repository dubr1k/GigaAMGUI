"""Pseudo-streaming ASR work scheduling independent of capture and Qt."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
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
    voiced_parts: list[int] = field(default_factory=list)
    """Voiced samples of each `audio` part, so a forced cut can split the count."""


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


FRAME_SAMPLES = 320
"""The gate decides per 20 ms frame at 16 kHz, whatever size the capture chunks are."""
PRE_ROLL_SAMPLES = 4_800
"""Audio kept ahead of the gate so a run starts before the first loud frame.

Word onsets (unvoiced consonants, a quiet first syllable) sit below the attack
threshold; without pre-roll the decoder never heard them."""
PAUSE_PARTIAL_SECONDS = 0.4
"""A pause this long shows the phrase as a draft instead of waiting for the final."""
MIN_SPEECH_SAMPLES = 3_200
"""Voiced audio a run needs before its text is published (0.2 s).

It used to be a full second of voiced 100 ms chunks plus at least two words,
which silently dropped every short phrase: «Привет! Как у тебя дела?» from a
quiet microphone measured 0.4 s and was decoded correctly, then discarded."""
MAX_UTTERANCE_SECONDS = 25
"""Longest run that is decoded as one window.

A run used to end only after 3 s below the gate, so continuous audio (a
lecture, music, a TV) kept one run open for the whole session: hundreds of
partials, not one final, nothing journaled, and at stop the entire session
went to the model as a single window — the backends have no long-form split,
so that ran out of memory or took minutes, and a failure lost the transcript.
"""
IDLE_FINAL_SECONDS = 5.0
"""Wall time without any audio after which an open run is finalized.

A run ends after 3 s of *audio* below the gate, but a WASAPI loopback goes
quiet by sending nothing: the silence that would end the run never arrives,
and the last phrase before a pause stayed a draft until the next sound."""
OFFSET_JUMP_SAMPLES = 1_600
"""A forward jump in a source's offsets larger than this (0.1 s) ends its run.

The timeline jumps over time a source spent silent without delivering; the
run before the jump is over, and joining audio across it would decode the
two sides as one window with offsets that no longer match the audio."""
CUT_SEARCH_SECONDS = 5
"""How far back from the limit a forced cut looks for the quietest frame.

Cutting exactly at the limit splits a word; within the last few seconds there
is almost always a pause between words to cut at instead."""


class _EnergyGate:
    """Adapt to the room floor without treating low-level noise as speech.

    Deciding per chunk averaged a syllable with the quiet around it: GigaAM
    Liquid sends 100 ms chunks, and a quiet speaker's 40 ms syllable at RMS
    0.02 came out near 0.013 — below the attack threshold — so whole phrases
    never opened a run. Frames make the gate independent of chunk size.
    """

    # Per-frame smoothing that keeps the ~2 s floor time constant the gate had
    # with 100 ms chunks (0.95 per chunk ≈ 0.99 per 20 ms frame).
    _FLOOR_ALPHA = 0.01
    # While open the floor follows the quietest frame of the last two seconds
    # with a ~30 s time constant. Adapting only while closed meant steady
    # noise above the release threshold (a fan, music) held the gate open for
    # the whole session. The minimum, not the level, is what it follows:
    # speech always has quiet frames between words, so a talker is never
    # learned as the room — only sound that never dips is.
    _OPEN_FLOOR_ALPHA = 0.02 / 30
    _FLOOR_WINDOW_FRAMES = 100

    def __init__(self) -> None:
        self._noise_floor = 0.001
        self._active = False
        self._frame_index = 0
        self._window: deque[tuple[int, float]] = deque()

    def voiced_samples(self, audio: np.ndarray) -> int:
        voiced = 0
        for start in range(0, len(audio), FRAME_SAMPLES):
            frame = audio[start:start + FRAME_SAMPLES]
            rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
            quietest = self._remember(rms)
            attack = max(0.015, self._noise_floor * 4)
            release = max(0.010, self._noise_floor * 2.5)
            self._active = rms >= (release if self._active else attack)
            if self._active:
                voiced += len(frame)
                self._noise_floor += (quietest - self._noise_floor) * self._OPEN_FLOOR_ALPHA
            else:
                self._noise_floor += (rms - self._noise_floor) * self._FLOOR_ALPHA
        return voiced

    def _remember(self, rms: float) -> float:
        """Sliding minimum of frame RMS over the last `_FLOOR_WINDOW_FRAMES`."""
        index = self._frame_index
        self._frame_index += 1
        while self._window and self._window[-1][1] >= rms:
            self._window.pop()
        self._window.append((index, rms))
        while self._window[0][0] <= index - self._FLOOR_WINDOW_FRAMES:
            self._window.popleft()
        return self._window[0][1]


def _normalize_word(word: str) -> str:
    return word.casefold().strip(".,!?;:…-–—«»\"'()")


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
        idle_final_seconds: float = IDLE_FINAL_SECONDS,
        on_partial: Callable[[TranscriptEvent], None] | None = None,
        on_final: Callable[[TranscriptEvent], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        if min(
            partial_delay_seconds, partial_context_seconds, partial_minimum_seconds, final_silence_seconds,
            idle_final_seconds,
        ) <= 0:
            raise ValueError("live ASR timing values must be positive")
        if partial_minimum_seconds > partial_context_seconds:
            raise ValueError("partial minimum cannot exceed partial context")
        self._backend = backend
        self._partial_delay_seconds = partial_delay_seconds
        self._partial_context_seconds = partial_context_seconds
        self._partial_minimum_seconds = partial_minimum_seconds
        self._final_silence_seconds = final_silence_seconds
        self._idle_final_seconds = idle_final_seconds
        self._next_offsets: dict[CaptureSource, int] = {}
        self._last_audio_at: dict[CaptureSource, float] = {}
        self._on_partial = on_partial
        self._on_final = on_final
        self._on_error = on_error
        self._runs: dict[CaptureSource, _SpeechRun] = {}
        self._energy_gates: dict[CaptureSource, _EnergyGate] = {}
        self._pre_roll: dict[CaptureSource, deque[np.ndarray]] = {}
        self._final_jobs: deque[_Job] = deque()
        self._partial_job: _Job | None = None
        self._partial_revisions: dict[str, int] = {}
        self._partial_hypotheses: dict[str, _Hypothesis] = {}
        self._refresh_seconds = partial_delay_seconds
        self._closed = False
        self._aborted = False
        self._condition = threading.Condition()
        self._worker = threading.Thread(target=self._run, name="live-asr", daemon=True)
        self._worker.start()

    @property
    def refresh_seconds(self) -> float:
        with self._condition:
            return self._refresh_seconds

    def submit(self, chunk: PcmChunk) -> None:
        if chunk.sample_rate != 16_000 or chunk.channels != 1:
            raise ValueError("live ASR requires derived 16 kHz mono chunks")
        audio = chunk.frames[:, 0]
        voiced = self._energy_gates.setdefault(chunk.source, _EnergyGate()).voiced_samples(audio)
        with self._condition:
            if self._closed:
                raise RuntimeError("scheduler is closed")
            expected = self._next_offsets.get(chunk.source)
            if expected is not None and chunk.sample_offset - expected > OFFSET_JUMP_SAMPLES:
                jumped = self._runs.get(chunk.source)
                if jumped is not None:
                    self._queue_final(chunk.source, jumped, paragraph_break_after=True)
                self._pre_roll.pop(chunk.source, None)
            self._next_offsets[chunk.source] = chunk.sample_offset + len(audio)
            self._last_audio_at[chunk.source] = time.monotonic()
            run = self._runs.get(chunk.source)
            if voiced:
                if run is None:
                    pre_roll = self._take_pre_roll(chunk.source)
                    run = _SpeechRun(
                        chunk.sample_offset - len(pre_roll),
                        chunk.sample_offset,
                        [pre_roll] if len(pre_roll) else [],
                        voiced_parts=[0] if len(pre_roll) else [],
                    )
                    self._runs[chunk.source] = run
                    # The worker sleeps without a timeout while no run is
                    # open; it must start timing this one.
                    self._condition.notify()
                run.audio.append(audio.copy())
                run.voiced_parts.append(voiced)
                run.end = chunk.sample_offset + len(audio)
                run.voiced_samples += voiced
                run.silence_start = None
                run.silence_samples = 0
                if run.end - run.start >= MAX_UTTERANCE_SECONDS * chunk.sample_rate:
                    run = self._cut_run(chunk.source, run)
                if self._should_refresh_partial(run, chunk.sample_rate):
                    self._schedule_partial(chunk.source, run)
            elif run is not None:
                run.audio.append(audio.copy())
                run.voiced_parts.append(0)
                run.end = chunk.sample_offset + len(audio)
                if run.silence_start is None:
                    run.silence_start = chunk.sample_offset
                run.silence_samples += len(audio)
                if run.silence_samples >= self._final_silence_seconds * chunk.sample_rate:
                    self._queue_final(chunk.source, run, paragraph_break_after=True)
                elif run.end - run.start >= MAX_UTTERANCE_SECONDS * chunk.sample_rate:
                    self._cut_run(chunk.source, run)
                elif (
                    run.silence_samples >= PAUSE_PARTIAL_SECONDS * chunk.sample_rate
                    and (run.last_partial_end is None or run.last_partial_end <= run.silence_start)
                ):
                    # A phrase shorter than the partial cadence would otherwise
                    # stay invisible until the final, seconds after it ended.
                    self._schedule_partial(chunk.source, run)
            else:
                self._keep_pre_roll(chunk.source, audio)

    def flush(self) -> None:
        with self._condition:
            for source, run in list(self._runs.items()):
                self._queue_final(source, run)
            self._condition.notify_all()

    def record_decode_duration(self, seconds: float) -> None:
        with self._condition:
            self._refresh_seconds = min(1.5, max(0.25, seconds))

    def close(self, timeout: float | None = None) -> bool:
        """Drain queued decodes; `timeout` bounds the wait, `None` waits fully.

        Abandoning the queue here used to drop every final that had not been
        decoded within a second, which silently emptied short sessions.
        Returns whether the queue was drained; on False the caller decides
        whether to keep waiting or to `abort()`.
        """
        self.flush()
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        self._worker.join(timeout=timeout)
        return not self._worker.is_alive()

    def abort(self) -> None:
        """Drop queued work and publish nothing more, including the decode in flight.

        A decode that never returns (a wedged GPU backend) must not hold stop()
        forever; whatever it produces after the session has exported would only
        land in a closed journal.
        """
        with self._condition:
            self._aborted = True
            self._closed = True
            self._final_jobs.clear()
            self._partial_job = None
            self._runs.clear()
            self._condition.notify_all()

    def _queue_final(
        self, source: CaptureSource, run: _SpeechRun, *, paragraph_break_after: bool = False,
    ) -> None:
        """End `run` with a final; its queued draft must not outlive it.

        The worker takes finals before the pending partial, so a draft queued
        for the same run used to be decoded after its final and published as
        a newer revision — Liquid showed it as a draft repeating the phrase.
        """
        self._final_jobs.append(
            self._job(source, run, is_final=True, paragraph_break_after=paragraph_break_after)
        )
        partial = self._partial_job
        if partial is not None and partial.source is source and partial.event_start == run.start:
            self._partial_job = None
        if self._runs.get(source) is run:
            del self._runs[source]
        self._condition.notify()

    def _finalize_idle_runs(self) -> None:
        now = time.monotonic()
        for source, run in list(self._runs.items()):
            if now - self._last_audio_at.get(source, now) >= self._idle_final_seconds:
                self._queue_final(source, run, paragraph_break_after=True)

    def _cut_run(self, source: CaptureSource, run: _SpeechRun) -> _SpeechRun:
        """Finalize a run that reached the length limit; the rest starts a new run.

        The cut goes to the quietest 20 ms frame of the last
        `CUT_SEARCH_SECONDS` before the limit (the latest one on a tie), which
        in speech is a pause between words.
        """
        audio = np.concatenate(run.audio)
        limit = min(len(audio), MAX_UTTERANCE_SECONDS * 16_000)
        search_from = max(FRAME_SAMPLES, limit - CUT_SEARCH_SECONDS * 16_000)
        frame_count = (limit - search_from) // FRAME_SAMPLES
        if frame_count > 0:
            window = audio[search_from:search_from + frame_count * FRAME_SAMPLES].astype(np.float64)
            levels = np.sqrt(np.mean(np.square(window.reshape(frame_count, -1)), axis=1))
            quietest = frame_count - 1 - int(np.argmin(levels[::-1]))
            cut = search_from + quietest * FRAME_SAMPLES
        else:
            cut = limit
        head_voiced = 0
        consumed = 0
        for part, part_voiced in zip(run.audio, run.voiced_parts, strict=False):
            if consumed + len(part) <= cut:
                head_voiced += part_voiced
            elif consumed < cut and len(part):
                head_voiced += round(part_voiced * (cut - consumed) / len(part))
            consumed += len(part)
        head = _SpeechRun(run.start, run.start + cut, [audio[:cut]], voiced_samples=head_voiced)
        self._queue_final(source, head)
        tail_voiced = max(0, run.voiced_samples - head_voiced)
        rest = _SpeechRun(
            run.start + cut,
            run.end,
            [audio[cut:]],
            voiced_samples=tail_voiced,
            voiced_parts=[tail_voiced],
        )
        if run.silence_start is not None:
            rest.silence_start = max(run.silence_start, rest.start)
            rest.silence_samples = min(run.silence_samples, rest.end - rest.start)
        self._runs[source] = rest
        return rest

    def _schedule_partial(self, source: CaptureSource, run: _SpeechRun) -> None:
        self._partial_job = self._job(source, run, is_final=False)
        run.last_partial_end = run.end
        self._condition.notify()

    def _keep_pre_roll(self, source: CaptureSource, audio: np.ndarray) -> None:
        buffered = self._pre_roll.setdefault(source, deque())
        buffered.append(audio.copy())
        total = sum(len(part) for part in buffered)
        while buffered and total - len(buffered[0]) >= PRE_ROLL_SAMPLES:
            total -= len(buffered.popleft())

    def _take_pre_roll(self, source: CaptureSource) -> np.ndarray:
        buffered = self._pre_roll.pop(source, None)
        if not buffered:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(buffered)[-PRE_ROLL_SAMPLES:]

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
                    self._finalize_idle_runs()
                    if self._final_jobs:
                        break
                    # Open runs need the clock to end them; with none open
                    # there is nothing to time and the worker just sleeps.
                    self._condition.wait(
                        timeout=min(0.5, max(0.05, self._idle_final_seconds / 4)) if self._runs else None
                    )
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
                if self._on_error is not None and not self._aborted:
                    self._on_error(exc)
            finally:
                self.record_decode_duration(time.monotonic() - started)

    def _publish(self, job: _Job, segments: list[TranscriptionSegment]) -> None:
        if not segments or self._aborted:
            return
        text = " ".join(segment["transcription"].strip() for segment in segments).strip()
        if not text:
            return
        if not self._has_acceptable_confidence(segments):
            return
        # Partials pass the same bar as finals: a draft whose final is then
        # rejected would otherwise stay on screen with nothing to replace it.
        if job.voiced_samples < MIN_SPEECH_SAMPLES:
            return
        event_id = f"{job.source.value}-{job.event_start}"
        if not job.is_final:
            text = self._reconcile_partial(event_id, text, trimmed=job.start > job.event_start)
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

    def _reconcile_partial(self, event_id: str, text: str, *, trimmed: bool) -> str:
        """Merge a new draft with the previous one for the same run.

        Words are compared without case and punctuation. Comparing them raw
        made «тебя?» → «тебя дела?» look like a contradiction, and the merge
        appended the new tail after the old one: «Как у тебя? тебя дела?».
        """
        incoming = text.split()
        hypothesis = self._partial_hypotheses.get(event_id)
        if hypothesis is None:
            self._partial_hypotheses[event_id] = _Hypothesis([], incoming)
            return text

        if trimmed:
            # The window slid past the start of the run: the decode no longer
            # contains the first words, so splice it onto where it begins.
            # Without an anchor there is no safe place to splice, and a frozen
            # draft is better than a duplicated one; the final replaces it.
            anchor = self._anchor(hypothesis.words, incoming)
            if anchor is None:
                return " ".join(hypothesis.words)
            position, skipped = anchor
            committed = hypothesis.words[:position]
            words = committed + incoming[skipped:]
            self._partial_hypotheses[event_id] = _Hypothesis(committed, words)
            return " ".join(words)

        common = self._common_prefix_length(hypothesis.words, incoming)
        if common >= len(hypothesis.committed):
            committed = incoming[:common]
            words = incoming
        elif self._is_repeated_regression(incoming[common:]):
            return " ".join(hypothesis.words)
        else:
            # The window still covers the whole run, so the newer decode has
            # strictly more evidence than the old prefix.
            committed = incoming[:common]
            words = incoming
        self._partial_hypotheses[event_id] = _Hypothesis(committed, words)
        return " ".join(words)

    @staticmethod
    def _anchor(previous: list[str], incoming: list[str]) -> tuple[int, int] | None:
        """Find where ``incoming`` starts inside ``previous``.

        Returns (index in previous, words skipped at the front of incoming).
        The window edge can cut a word in half, so the first word or two of
        the incoming decode may be a fragment («занимаешься» for «Чем
        занимаешься»); anchors start from the first, second or third word.
        Among several matches the one nearest the expected position wins —
        the new window starts roughly where the previous tail did.
        """
        # Bare punctuation tokens («—») carry no words and must not break a match.
        before = [(index, norm) for index, word in enumerate(previous) if (norm := _normalize_word(word))]
        after = [(index, norm) for index, word in enumerate(incoming) if (norm := _normalize_word(word))]
        before_words = [norm for _, norm in before]
        after_words = [norm for _, norm in after]
        expected = max(0, len(before_words) - len(after_words))
        for skipped in range(min(3, len(after_words))):
            for size in (3, 2):
                key = after_words[skipped:skipped + size]
                if len(key) < size:
                    continue
                matches = [
                    index for index in range(len(before_words) - size + 1)
                    if before_words[index:index + size] == key
                ]
                if matches:
                    best = min(matches, key=lambda index: abs(index - expected))
                    return before[best][0], after[skipped][0]
        return None

    @staticmethod
    def _common_prefix_length(left: list[str], right: list[str]) -> int:
        length = 0
        for previous, current in zip(left, right, strict=False):
            if _normalize_word(previous) != _normalize_word(current):
                break
            length += 1
        return length

    @staticmethod
    def _is_repeated_regression(words: list[str]) -> bool:
        normalized = [_normalize_word(word) for word in words]
        return len(normalized) >= 2 and len(set(normalized)) == 1

    @staticmethod
    def _has_acceptable_confidence(segments: list[TranscriptionSegment]) -> bool:
        confidences = [
            confidence for segment in segments
            if isinstance((confidence := segment.get("confidence")), (int, float))
        ]
        return not confidences or min(confidences) >= 0.45
