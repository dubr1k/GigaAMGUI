"""Source-isolated live capture lifecycle coordinator."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from time import monotonic
from typing import Protocol

from src.core.asr.types import normalize_window_audio

from .capture.base import CaptureAdapter
from .diagnostics import SessionLog
from .diarization import (
    LIVE_ESTIMATE_BACKEND,
    LIVE_ESTIMATE_STABILIZATION_HORIZON_SECONDS,
    BuiltinDiarizers,
    label_event,
)
from .exports import ExportSelection, export_session
from .journal import ConversationJournal, EventJournal, LiveSessionStore
from .recorder import SessionRecorder, join_segments
from .timeline import AlignedMixer, SourceTimeline
from .types import (
    CaptureEvent,
    CaptureEventKind,
    CaptureSource,
    CaptureState,
    DiarizationMode,
    LiveSettings,
    PcmChunk,
    TranscriptEvent,
)


class AsrScheduler(Protocol):
    def submit(self, chunk: PcmChunk) -> None: ...

    def flush(self) -> None: ...

    def close(self, timeout: float | None = None) -> bool | None: ...


@dataclass(frozen=True)
class LiveStatus:
    state: CaptureState
    active_sources: set[CaptureSource]
    failed_sources: set[CaptureSource]


@dataclass(frozen=True)
class SessionResult:
    session_dir: Path
    recordings: dict[CaptureSource, Path]
    exports: list[Path]
    errors: list[str] = field(default_factory=list)
    """Stages of stop() that failed; the rest still ran (see `LiveSession.stop`)."""
    recording_files: dict[str, list[Path]] = field(default_factory=dict)
    """Every segment of every track, "mix" included; `recordings` has the first only."""


@dataclass(frozen=True)
class ConversationTurn:
    id: str
    question: str
    answer: str = ""
    status: str = "generating"


SchedulerFactory = Callable[
    [CaptureSource, Callable[[TranscriptEvent], None], Callable[[TranscriptEvent], None], Callable[[Exception], None]], AsrScheduler
]

MAX_MIX_SKEW_NS = 1_000_000_000
MAX_SOURCE_START_DELAY_NS = 5_000_000_000
"""Largest start delay between sources that is taken from their clocks."""
MAX_PENDING_MIX_CHUNKS = 100
MAX_RECORDING_FAILURES = 5
"""Consecutive write failures before a source's recording is given up on.

A writer that cannot open its file does not start working later, and retrying
per chunk turns one fault into thousands of identical log lines — issue #48
produced 15 255 of them in a single session."""
CHECKPOINT_INTERVAL_SECONDS = 2.0
STOP_DRAIN_TIMEOUT_SECONDS = 120.0
"""How long stop() waits for each source's queued decodes.

Stopping drains every queued final (issue #42), but a decode that never
returns — a wedged GPU backend — must not keep the session in STOPPING for
ever: after this the undecoded speech is abandoned and the session still
closes its recordings and writes its exports."""
# How long a source that has already produced audio may stay quiet before the
# mixer stops waiting for it, and how long to wait for a source that has never
# produced anything at all (an idle WASAPI loopback endpoint, typically).
MIX_SOURCE_IDLE_SECONDS = 0.5
MIX_SOURCE_STARTUP_GRACE_SECONDS = 1.0


class LiveSession:
    """Own capture lifecycle while leaving ASR work on scheduler-owned threads."""

    def __init__(
        self,
        root_dir: Path,
        settings: LiveSettings,
        adapters: Mapping[CaptureSource, CaptureAdapter],
        *,
        scheduler_factory: SchedulerFactory,
        export_selection: ExportSelection | None = None,
        recorder_factory: Callable[[Path, bool | set[CaptureSource], bool], SessionRecorder] = SessionRecorder,
        diarization_factory: Callable[[str], object] | None = None,
        translate: Callable[[str, str], str] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._settings = settings
        self._adapters = dict(adapters)
        self._scheduler_factory = scheduler_factory
        self._export_selection = replace(
            export_selection or ExportSelection(),
            sample_rate=settings.asr_sample_rate,
        )
        self._recorder_factory = recorder_factory
        self._diarization_factory = diarization_factory or BuiltinDiarizers()
        self._translate = translate or (lambda _ru, en: en)
        self._session_dir = LiveSessionStore(root_dir).create(settings)
        self._log_sink = log
        self._session_log = SessionLog(self._session_dir / "live.log")
        self._journal = EventJournal(self._session_dir / "events.jsonl")
        self._conversation_journal = ConversationJournal(self._session_dir / "conversation.jsonl")
        self._recorder = recorder_factory(
            self._session_dir,
            {
                source
                for source, selected in (
                    (CaptureSource.MIC, settings.record_mic_audio),
                    (CaptureSource.SYSTEM, settings.record_system_audio),
                )
                if settings.record_source_audio and selected
            },
            settings.record_mix_audio,
        )
        self._state = CaptureState.IDLE
        self._started_adapters: dict[CaptureSource, CaptureAdapter] = {}
        self._active_sources: set[CaptureSource] = set()
        self._failed_sources: set[CaptureSource] = set()
        self._timelines: dict[CaptureSource, SourceTimeline] = {}
        self._mix_inputs: dict[CaptureSource, list[PcmChunk]] = {}
        self._mix_offset_origins: dict[CaptureSource, int] = {}
        self._mix_source_origins_ns: dict[CaptureSource, int] = {}
        self._mix_last_input_at: dict[CaptureSource, float] = {}
        self._mix_stalled_sources: set[CaptureSource] = set()
        self._reported_mix_stalls: set[CaptureSource] = set()
        self._mix_started_at = float("inf")
        self._mix_session_origin_ns: int | None = None
        self._mixer = AlignedMixer(max_skew_seconds=MAX_MIX_SKEW_NS / 1_000_000_000)
        self._last_mix_error_ns: int | None = None
        self._mix_recording_enabled = settings.record_mix_audio
        self._schedulers: dict[CaptureSource, AsrScheduler] = {}
        self._live_diarizers: dict[CaptureSource, object] = {}
        self._live_diarization_unavailable: set[CaptureSource] = set()
        self._speaker_labels: dict[tuple[CaptureSource, str], str] = {}
        self._finalized_revisions: dict[tuple[CaptureSource, str], int] = {}
        self._partials: dict[CaptureSource, TranscriptEvent] = {}
        self._conversation: list[ConversationTurn] = []
        self._conversation_frozen = False
        self._subscribers: list[Callable[[TranscriptEvent | CaptureEvent | LiveStatus], None]] = []
        self._reported_recording_failures: set[str] = set()
        self._recording_failures: dict[CaptureSource, int] = {}
        self._recording_disabled: set[CaptureSource] = set()
        self._last_checkpoint_at = float("-inf")
        self._lock = RLock()

    @property
    def session_dir(self) -> Path:
        return self._session_dir

    def log(self, message: str) -> None:
        """Record a live-path diagnostic to the session log and the UI sink."""
        self._session_log.write(message)
        if self._log_sink is None:
            return
        try:
            self._log_sink(message)
        except Exception:
            self._log_sink = None

    def start(self) -> None:
        with self._lock:
            if self._state is not CaptureState.IDLE:
                raise RuntimeError("session has already started")
            self.log(f"session start: dir={self._session_dir} sources={sorted(s.value for s in self._adapters)}")
            self._state = CaptureState.STARTING
            self._mix_started_at = monotonic()
            for source, adapter in self._adapters.items():
                self._schedulers[source] = self._scheduler_factory(
                    source,
                    self._on_final,
                    self._on_partial,
                    lambda error, source=source: self._on_asr_error(source, error),
                )
                # Native adapters can emit an asynchronous permission/device event
                # during start; mark source active before that callback can arrive.
                self._active_sources.add(source)
                # A start that raised may still have left a thread or a native
                # stream behind; stop() owns every adapter it tried to start.
                self._started_adapters[source] = adapter
                try:
                    adapter.start(self._on_chunk, self._on_event)
                except Exception as exc:
                    self._mark_failed(source, str(exc))
            self._state = CaptureState.RECORDING if self._active_sources else CaptureState.FAILED
            self._notify_status()

    def pause(self) -> None:
        with self._lock:
            if self._state is not CaptureState.RECORDING:
                raise RuntimeError("only a recording session can be paused")
            for source in self._active_sources:
                self._adapters[source].pause()
            self._state = CaptureState.PAUSED
            self._notify_status()

    def resume(self) -> None:
        with self._lock:
            if self._state is not CaptureState.PAUSED:
                raise RuntimeError("only a paused session can be resumed")
            for source in self._active_sources:
                self._adapters[source].resume()
            self._state = CaptureState.RECORDING
            self._notify_status()

    def stop(self) -> SessionResult:
        """Stop capture, drain ASR, close recordings and write exports.

        Every stage runs even when an earlier one fails: an adapter that throws
        on stop used to abort the rest, leaving the FLAC files unfinalized, no
        exports and the session stuck in STOPPING. Failures are collected into
        `SessionResult.errors`, logged and reported as one status event; the
        session always ends STOPPED.
        """
        with self._lock:
            if self._state in {CaptureState.IDLE, CaptureState.STOPPING, CaptureState.STOPPED}:
                raise RuntimeError("session is not running")
            self._state = CaptureState.STOPPING
            # Failed sources included: they still own a dispatch thread and,
            # on macOS, an SCStream that keeps the screen-recording indicator on.
            adapters = list(self._started_adapters.items())
            schedulers = list(self._schedulers.items())
        errors: list[str] = []
        recordings: dict[CaptureSource, Path] = {}
        recording_files: dict[str, list[Path]] = {}
        exports: list[Path] = []
        try:
            self._notify_status()
            # Draining happens outside the lock: the ASR workers publish finals
            # through _on_final, which needs the same lock, and exports must not
            # run until the last decode has landed in the journal.
            for source, adapter in adapters:
                self._attempt(errors, f"stop {source.value} capture", adapter.stop)
            self.log(f"draining {len(schedulers)} asr scheduler(s) before export")
            for source, scheduler in schedulers:
                self._attempt(errors, f"flush {source.value} recognition", scheduler.flush)
            for source, scheduler in schedulers:
                self._attempt(
                    errors, f"drain {source.value} recognition",
                    lambda source=source, scheduler=scheduler: self._drain_scheduler(source, scheduler),
                )
            with self._lock:
                self._attempt(errors, "flush mix", self._flush_mix_inputs)
                recordings = self._close_recorder(errors)
                recording_files = self._recording_files(recordings)
                self._attempt(errors, "update metadata", self._record_artifacts)
                if self._settings.diarization_mode is DiarizationMode.AFTER_STOP:
                    self._attempt(errors, "diarize", lambda: self._diarize_recordings(recordings, recording_files))
                self._attempt(errors, "freeze conversation", self._freeze_conversation)
                exports = self._attempt(
                    errors, "export",
                    lambda: export_session(self._session_dir, self._journal.latest_events(), self._export_selection),
                ) or []
        finally:
            with self._lock:
                self._active_sources.clear()
                self._state = CaptureState.STOPPED
        if errors:
            self._notify(CaptureEvent(
                CaptureEventKind.STATUS,
                CaptureSource.MIC,
                0,
                0,
                self._translate(
                    "Сессия остановлена с ошибками: " + "; ".join(errors),
                    "Session stopped with errors: " + "; ".join(errors),
                ),
            ))
        self._notify_status()
        return SessionResult(self._session_dir, recordings, exports, errors, recording_files)

    def _attempt(self, errors: list[str], stage: str, action: Callable[[], object]):
        try:
            return action()
        except Exception as exc:
            detail = f"{stage}: {type(exc).__name__}: {exc}"
            errors.append(detail)
            self.log(f"stop stage failed: {detail}")
            return None

    def _drain_scheduler(self, source: CaptureSource, scheduler: AsrScheduler) -> None:
        if _accepts_timeout(scheduler.close):
            drained = scheduler.close(timeout=STOP_DRAIN_TIMEOUT_SECONDS)
        else:
            drained = scheduler.close()
        if drained is not False:
            return
        abort = getattr(scheduler, "abort", None)
        if callable(abort):
            abort()
        raise TimeoutError(
            f"recognition did not finish within {STOP_DRAIN_TIMEOUT_SECONDS:g} s; "
            "undecoded speech was abandoned"
        )

    def _close_recorder(self, errors: list[str]) -> dict[CaptureSource, Path]:
        try:
            return self._recorder.close()
        except Exception as exc:
            detail = f"close recordings: {type(exc).__name__}: {exc}"
            errors.append(detail)
            self.log(f"stop stage failed: {detail}")
            return dict(getattr(exc, "recordings", {}) or {})

    def _recording_files(self, recordings: dict[CaptureSource, Path]) -> dict[str, list[Path]]:
        files = getattr(self._recorder, "recording_files", None)
        if callable(files):
            try:
                return {track: list(paths) for track, paths in files().items()}
            except Exception as exc:
                self.log(f"recording list unavailable: {type(exc).__name__}: {exc}")
        return {source.value: [path] for source, path in recordings.items()}

    def _record_artifacts(self) -> None:
        artifacts = getattr(self._recorder, "artifacts", None)
        if callable(artifacts):
            LiveSessionStore(self._session_dir.parent).update_metadata(
                self._session_dir,
                recordings=artifacts(),
            )

    def status(self) -> LiveStatus:
        with self._lock:
            return LiveStatus(self._state, set(self._active_sources), set(self._failed_sources))

    def ask_context(self) -> str:
        # Called from the assistant's thread while ASR threads publish: the
        # drafts are copied under the lock instead of iterated live.
        with self._lock:
            events = self._journal.latest_events()
            partials = list(self._partials.items())
        final_text = "\n".join(
            f"[{datetime.fromtimestamp(event.timestamp_ns / 1_000_000_000, timezone.utc).isoformat()}] "
            f"{event.source_label}{f' / {event.speaker}' if event.speaker else ''}: {event.text}"
            for event in events
            if event.status == "final"
        )
        drafts = "\n".join(
            f"[{source.value.upper()} draft] {event.text}"
            for source, event in partials
        )
        if not drafts:
            return final_text
        return f"Final transcript:\n{final_text}\n\nDraft transcript:\n{drafts}"

    def begin_conversation(self, question: str) -> ConversationTurn:
        with self._lock:
            self._require_conversation_open()
            turn = ConversationTurn(f"conversation-{len(self._conversation)}", question)
            self._conversation.append(turn)
            return turn

    def append_conversation_answer(self, turn_id: str, text: str) -> None:
        with self._lock:
            self._require_conversation_open()
            turn = self._conversation_turn(turn_id)
            self._replace_conversation_turn(replace(turn, answer=turn.answer + text))

    def finish_conversation(self, turn_id: str, answer: str | None = None, *, status: str = "complete") -> None:
        with self._lock:
            self._require_conversation_open()
            turn = self._conversation_turn(turn_id)
            turn = replace(turn, answer=turn.answer if answer is None else answer, status=status)
            self._replace_conversation_turn(turn)
            self._conversation_journal.append(turn)

    def cancel_conversation(self, turn_id: str) -> None:
        self.finish_conversation(turn_id, "", status="cancelled")

    def clear_conversation(self) -> None:
        with self._lock:
            self._require_conversation_open()
            self._conversation.clear()
            self._conversation_journal.clear()

    def conversation(self) -> list[ConversationTurn]:
        with self._lock:
            return list(self._conversation)

    def subscribe(self, callback: Callable[[TranscriptEvent | CaptureEvent | LiveStatus], None]) -> None:
        with self._lock:
            self._subscribers.append(callback)

    def _on_chunk(self, chunk: PcmChunk) -> None:
        with self._lock:
            if self._state is not CaptureState.RECORDING or chunk.source not in self._active_sources:
                return
            timeline = self._timelines.setdefault(
                chunk.source,
                SourceTimeline(chunk.source, chunk.sample_rate, chunk.channels, self._on_event),
            )
            for aligned in timeline.ingest(chunk):
                # Recording is best-effort: losing the FLAC track must not cost
                # us the recognition stream that the user actually came for.
                if aligned.source not in self._recording_disabled:
                    try:
                        self._recorder.write(aligned)
                    except Exception as exc:
                        self._report_recording_failure(aligned, exc)
                    else:
                        self._recording_failures.pop(aligned.source, None)
                if self._mix_recording_enabled:
                    pending = self._mix_inputs.setdefault(aligned.source, [])
                    pending.append(self._normalize_mix_timestamp(aligned))
                    self._mix_last_input_at[aligned.source] = monotonic()
                    self._mix_stalled_sources.discard(aligned.source)
                    if len(pending) > MAX_PENDING_MIX_CHUNKS:
                        self._mark_stalled_mix_peers(aligned)
                    self._write_ready_mixes()
                # All channels, downmixed: channel 0 alone missed a talker on
                # input 2 of a stereo interface entirely.
                audio = normalize_window_audio(aligned.frames, aligned.sample_rate, self._settings.asr_sample_rate)
                offset = round(aligned.sample_offset * self._settings.asr_sample_rate / aligned.sample_rate)
                self._schedulers[aligned.source].submit(
                    PcmChunk(
                        aligned.source,
                        self._settings.asr_sample_rate,
                        1,
                        offset,
                        audio[:, None].copy(),
                        aligned.timestamp_ns,
                    )
                )
            self._write_checkpoint_if_due()

    def _write_checkpoint_if_due(self) -> None:
        """Checkpointing every chunk meant ~50 disk writes/s per source."""
        now = monotonic()
        if now - self._last_checkpoint_at < CHECKPOINT_INTERVAL_SECONDS:
            return
        self._last_checkpoint_at = now
        try:
            LiveSessionStore(self._session_dir.parent).write_checkpoint(
                self._session_dir,
                {"active_sources": sorted(source.value for source in self._active_sources)},
            )
        except Exception as exc:
            self.log(f"checkpoint write failed: {type(exc).__name__}: {exc}")

    def _normalize_mix_timestamp(self, chunk: PcmChunk) -> PcmChunk:
        """Position a chunk on the mix timeline by its audio, not by wall clock.

        Capture timestamps are arrival times, so they jitter by tens of
        milliseconds even when both streams are perfectly continuous. The
        mixer takes the difference between the two sources at face value, and
        jitter has no sign there — every pair contributed ``abs(jitter)``, so
        even zero-mean jitter inflated the track (issue #50).

        Sample offsets come from ``SourceTimeline``, which has already filled
        gaps and trimmed overlaps, so they advance exactly with the audio. The
        one thing wall clock is still needed for — where each source starts —
        stays where it was: both sources are rebased onto the session origin at
        their first chunk.
        """
        if self._mix_session_origin_ns is None:
            self._mix_session_origin_ns = chunk.timestamp_ns
        if chunk.source not in self._mix_offset_origins:
            # Read the clock once per source, at its first chunk. Rebasing every
            # source onto the session origin instead erased the start delay of
            # a later source (SCStream starts ~0.5 s after the microphone), and
            # the system track played that much early in mix.flac.
            # Devices can also run on unrelated clocks (WASAPI endpoints have
            # their own epochs); a delay outside a plausible start-up window
            # says nothing about when the source started, so it starts at the
            # session origin as before.
            self._mix_offset_origins[chunk.source] = chunk.sample_offset
            delay_ns = chunk.timestamp_ns - self._mix_session_origin_ns
            self._mix_source_origins_ns[chunk.source] = self._mix_session_origin_ns + (
                delay_ns if 0 < delay_ns <= MAX_SOURCE_START_DELAY_NS else 0
            )
        origin_offset = self._mix_offset_origins[chunk.source]
        elapsed_ns = round((chunk.sample_offset - origin_offset) * 1_000_000_000 / chunk.sample_rate)
        return replace(chunk, timestamp_ns=self._mix_source_origins_ns[chunk.source] + elapsed_ns)

    def _mix_participants(self) -> set[CaptureSource]:
        """Sources the mixer should still wait for before writing a block.

        A source that never starts (an idle loopback endpoint) or that has gone
        quiet must not hold the mix track hostage — that starvation is what
        used to disable mixed recording seconds after the session began.
        """
        now = monotonic()
        participants: set[CaptureSource] = set()
        for source in self._active_sources:
            if source in self._mix_stalled_sources:
                continue
            last_input_at = self._mix_last_input_at.get(source)
            if last_input_at is None:
                if now - self._mix_started_at <= MIX_SOURCE_STARTUP_GRACE_SECONDS:
                    participants.add(source)
                continue
            if now - last_input_at <= MIX_SOURCE_IDLE_SECONDS:
                participants.add(source)
        return participants

    def _mark_stalled_mix_peers(self, chunk: PcmChunk) -> None:
        stalled = {
            source for source in self._active_sources
            if source is not chunk.source and not self._mix_inputs.get(source)
        }
        if not stalled:
            return
        self._mix_stalled_sources |= stalled
        for source in sorted(stalled, key=lambda item: item.value):
            self.log(f"mix peer stalled [{source.value}]: no audio delivered; mixing without it")
            if source in self._reported_mix_stalls:
                continue
            self._reported_mix_stalls.add(source)
            self._notify(CaptureEvent(
                CaptureEventKind.STATUS,
                source,
                chunk.sample_offset,
                chunk.timestamp_ns,
                self._translate(
                    f"Источник «{source.value}» не отдаёт звук — проверьте выбранное устройство. "
                    "Общая дорожка записывается без него.",
                    f"Source '{source.value}' is not delivering audio — check the selected device. "
                    "The combined track continues without it.",
                ),
            ))

    def _write_ready_mixes(self) -> None:
        if not self._mix_recording_enabled:
            return
        while True:
            participants = self._mix_participants()
            if not participants or not all(self._mix_inputs.get(source) for source in participants):
                return
            heads = {source: self._mix_inputs[source][0] for source in participants}
            earliest = min(heads.values(), key=lambda chunk: chunk.timestamp_ns)
            latest_timestamp_ns = max(chunk.timestamp_ns for chunk in heads.values())
            if latest_timestamp_ns - earliest.timestamp_ns > MAX_MIX_SKEW_NS:
                self._disable_mix_recording(
                    earliest.source,
                    earliest.timestamp_ns,
                    "timestamp skew exceeds 1.000s",
                )
                return
            # Mix in time order: a head that starts after the earliest one ends
            # waits for its turn. Popping one chunk from every source paired
            # chunks by index, so a source that started later stayed a constant
            # distance ahead of its peer instead of lining up with it.
            earliest_end_ns = earliest.timestamp_ns + round(
                len(earliest.frames) * 1_000_000_000 / earliest.sample_rate
            )
            inputs = {
                source: self._mix_inputs[source].pop(0)
                for source, head in heads.items()
                if head.timestamp_ns < earliest_end_ns
            }
            self._write_mix(inputs)

    def _flush_mix_inputs(self) -> None:
        if not self._mix_recording_enabled:
            self._mix_inputs.clear()
            return
        pending = [chunk for chunks in self._mix_inputs.values() for chunk in chunks]
        self._mix_inputs.clear()
        for chunk in sorted(pending, key=lambda item: item.timestamp_ns):
            self._write_mix({chunk.source: chunk})
        # The mixer holds back audio whose peer has not caught up yet; at stop
        # there is no peer left to wait for, and dropping it would truncate the
        # tail of the mix track.
        try:
            tail = self._mixer.flush()
        except Exception as exc:
            self.log(f"mix flush failed: {type(exc).__name__}: {exc}")
            return
        if tail is not None and len(tail.frames):
            try:
                self._recorder.write_mix(tail)
            except Exception as exc:
                self.log(f"mix flush failed: {type(exc).__name__}: {exc}")

    def _write_mix(self, chunks: Mapping[CaptureSource, PcmChunk]) -> None:
        try:
            self._recorder.write_mix(self._mixer.mix(chunks))
        except Exception as exc:
            timestamp_ns = min(chunk.timestamp_ns for chunk in chunks.values())
            self._disable_mix_recording(next(iter(chunks)), timestamp_ns, str(exc))

    def _disable_mix_recording(self, source: CaptureSource, timestamp_ns: int, reason: str) -> None:
        if not self._mix_recording_enabled:
            return
        self._mix_recording_enabled = False
        self._mix_inputs.clear()
        self.log(f"mix recording disabled [{source.value}]: {reason}")
        detail = self._translate(
            "Запись смешанного аудио отключена для этой сессии: "
            f"{reason}. Раздельная запись микрофона и системы, а также распознавание продолжаются.",
            "Mixed audio recording disabled for this session: "
            f"{reason}. Separate microphone and system recording and recognition continue.",
        )
        self._notify(CaptureEvent(CaptureEventKind.STATUS, source, 0, timestamp_ns, detail))

    def _report_mix_error(self, chunks: Mapping[CaptureSource, PcmChunk], detail: str) -> None:
        timestamp_ns = min(chunk.timestamp_ns for chunk in chunks.values())
        if self._last_mix_error_ns is None or timestamp_ns - self._last_mix_error_ns >= 5_000_000_000:
            self._last_mix_error_ns = timestamp_ns
            source = next(iter(chunks))
            self._notify(CaptureEvent(CaptureEventKind.STATUS, source, 0, timestamp_ns, detail))

    def _report_recording_failure(self, chunk: PcmChunk, exc: Exception) -> None:
        detail = f"{type(exc).__name__}: {exc}"
        self.log(f"recording write failed [{chunk.source.value}]: {detail}")
        failures = self._recording_failures.get(chunk.source, 0) + 1
        self._recording_failures[chunk.source] = failures
        if failures >= MAX_RECORDING_FAILURES:
            self._disable_source_recording(chunk, detail)
            return
        if detail in self._reported_recording_failures:
            return
        self._reported_recording_failures.add(detail)
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

    def _disable_source_recording(self, chunk: PcmChunk, reason: str) -> None:
        if chunk.source in self._recording_disabled:
            return
        self._recording_disabled.add(chunk.source)
        self.log(
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

    def _on_event(self, event: CaptureEvent) -> None:
        with self._lock:
            self.log(f"capture event [{event.source.value}/{event.kind.value}]: {event.detail}")
            if self._state in _FINISHING_STATES:
                # Streams report their own teardown; a late DEVICE_REMOVED used
                # to turn STOPPED into FAILED and re-enable Stop for a second
                # export of the same session.
                return
            if event.kind in {CaptureEventKind.PERMISSION_DENIED, CaptureEventKind.DEVICE_REMOVED, CaptureEventKind.DISK_FULL}:
                self._mark_failed(event.source, event.detail)
            self._notify(event)

    def _on_final(self, event: TranscriptEvent) -> None:
        with self._lock:
            self._partials.pop(event.source, None)
            finalized = label_event(event, self._settings.diarization_mode)
            self._record_finalized(finalized)
            self._journal.append(finalized)
            self._notify(finalized)
            if self._settings.diarization_mode is not DiarizationMode.LIVE_ESTIMATE:
                return
            recent = self._recent_events(finalized)
        # Creating the diarizer loads a model. Under the session lock that
        # stalled _on_chunk for every source, and the native client's backlog
        # overflowed and dropped audio meanwhile.
        estimates = self._estimate_live_speakers(finalized.source, recent)
        with self._lock:
            for revised in self._revised_speakers(recent, estimates):
                self._record_finalized(revised)
                self._journal.append(revised)
                self._notify(revised)

    def _on_partial(self, event: TranscriptEvent) -> None:
        with self._lock:
            # A final is terminal for its event: a draft of it that arrives
            # later — whatever its revision — would show the finished phrase
            # again as text still being spoken.
            if (event.source, event.event_id) in self._finalized_revisions:
                return
            self._partials[event.source] = event
            self._notify(event)

    def _require_conversation_open(self) -> None:
        if self._conversation_frozen:
            raise RuntimeError("conversation is frozen")

    def _conversation_turn(self, turn_id: str) -> ConversationTurn:
        for turn in self._conversation:
            if turn.id == turn_id:
                return turn
        raise KeyError(turn_id)

    def _replace_conversation_turn(self, updated: ConversationTurn) -> None:
        self._conversation = [updated if turn.id == updated.id else turn for turn in self._conversation]

    def _freeze_conversation(self) -> None:
        if self._conversation_frozen:
            return
        for turn in self._conversation:
            if turn.status == "generating":
                self._conversation_journal.append(replace(turn, status="frozen"))
        self._conversation_frozen = True

    def _record_finalized(self, event: TranscriptEvent) -> None:
        key = (event.source, event.event_id)
        self._finalized_revisions[key] = max(event.revision, self._finalized_revisions.get(key, -1))

    def _recent_events(self, event: TranscriptEvent) -> list[TranscriptEvent]:
        horizon_samples = LIVE_ESTIMATE_STABILIZATION_HORIZON_SECONDS * self._settings.asr_sample_rate
        return [
            item
            for item in self._journal.latest_events()
            if item.source is event.source and event.sample_end - item.sample_end <= horizon_samples
        ]

    def _estimate_live_speakers(self, source: CaptureSource, recent: list[TranscriptEvent]) -> dict:
        """Runs without the session lock; only the ASR thread of `source` calls it."""
        if source in self._live_diarization_unavailable:
            return {}
        if not getattr(self._diarization_factory, "supports_live_estimate", True):
            with self._lock:
                self._report_live_diarization_unavailable(
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
                diarizer = self._create_diarizer(LIVE_ESTIMATE_BACKEND)
                self._live_diarizers[source] = diarizer
            except Exception as exc:
                with self._lock:
                    self._report_live_diarization_unavailable(source, str(exc))
                return {}
        estimate = getattr(diarizer, "estimate_events", None)
        if not callable(estimate):
            with self._lock:
                self._report_live_diarization_unavailable(
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
                self._report_live_diarization_unavailable(source, str(exc))
            return {}

    def _diarize_recordings(
        self,
        recordings: dict[CaptureSource, Path],
        files: Mapping[str, list[Path]] | None = None,
    ) -> None:
        diarizer = None
        for source, path in recordings.items():
            parts = list((files or {}).get(source.value) or [path])
            joined = None
            try:
                # One model for every source: it used to be loaded per source.
                if diarizer is None:
                    diarizer = self._create_diarizer(self._settings.diarization_backend)
                # A rolled-over track is diarized as one file: the first
                # segment alone left everything after ~15 min unlabelled.
                if len(parts) > 1:
                    joined = join_segments(parts, self._session_dir / f".{source.value}-diarize.flac")
                segments = diarizer.diarize(str(joined or parts[0]))
                events = [event for event in self._journal.latest_events() if event.source is source]
                for revised in self._revised_speakers(events, self._segment_speakers(events, segments)):
                    self._record_finalized(revised)
                    self._journal.append(revised)
                    self._notify(revised)
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

    def _create_diarizer(self, backend: str):
        return self._diarization_factory(backend)

    def _segment_speakers(self, events, segments) -> dict[str, str]:
        speakers = {}
        for event in events:
            start = event.sample_start / self._settings.asr_sample_rate
            end = event.sample_end / self._settings.asr_sample_rate
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

    def _revised_speakers(self, events, estimates) -> list[TranscriptEvent]:
        revised = []
        for event in events:
            speaker = estimates.get(event.event_id)
            if speaker is None:
                continue
            anonymous = self._anonymous_speaker(event.source, str(speaker))
            if event.speaker != anonymous:
                revised.append(replace(event, revision=event.revision + 1, speaker=anonymous, supersedes=event.revision))
        return revised

    def _anonymous_speaker(self, source: CaptureSource, raw_speaker: str) -> str:
        key = (source, raw_speaker)
        number = len(self._speaker_labels) + 1
        return self._speaker_labels.setdefault(
            key,
            self._translate(f"Спикер {number}", f"Speaker {number}"),
        )

    def _report_live_diarization_unavailable(self, source: CaptureSource, detail: str) -> None:
        if source in self._live_diarization_unavailable:
            return
        self._live_diarization_unavailable.add(source)
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

    def _on_asr_error(self, source: CaptureSource, error: Exception) -> None:
        self.log(f"asr error [{source.value}]: {type(error).__name__}: {error}")
        self._notify(CaptureEvent(CaptureEventKind.STATUS, source, 0, 0, str(error)))

    def _mark_failed(self, source: CaptureSource, detail: str) -> None:
        self.log(f"source failed [{source.value}]: {detail}")
        if self._state in _FINISHING_STATES:
            return
        self._active_sources.discard(source)
        self._failed_sources.add(source)
        if not self._active_sources and self._state is not CaptureState.STARTING:
            self._state = CaptureState.FAILED
        self._notify(CaptureEvent(CaptureEventKind.STATUS, source, 0, 0, detail))

    def _notify_status(self) -> None:
        self._notify(self.status())

    def _notify(self, value: TranscriptEvent | CaptureEvent | LiveStatus) -> None:
        for callback in tuple(self._subscribers):
            # A subscriber that throws (a closed worker pipe, a deleted Qt
            # object) must not cost the session its stop or a capture thread.
            try:
                callback(value)
            except Exception as exc:
                self.log(f"subscriber failed: {type(exc).__name__}: {exc}")


_FINISHING_STATES = frozenset({CaptureState.STOPPING, CaptureState.STOPPED})


def _accepts_timeout(close: Callable[..., object]) -> bool:
    try:
        return "timeout" in inspect.signature(close).parameters
    except (TypeError, ValueError):
        return False
