"""Source-isolated live capture lifecycle coordinator."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import RLock
from time import monotonic
from typing import Protocol

from .capture.base import CaptureAdapter
from .conversation import ConversationLog, ConversationTurn
from .diagnostics import SessionLog
from .diarization import BuiltinDiarizers, SpeakerLabeler
from .exports import ExportSelection, export_session
from .journal import ConversationJournal, EventJournal, LiveSessionStore
from .mixing import MAX_MIX_SKEW_NS, MAX_PENDING_MIX_CHUNKS, MixCoordinator
from .recorder import SessionRecorder
from .recording_policy import MAX_RECORDING_FAILURES, RecordingGuard
from .timeline import AsrFeed, SourceTimeline
from .transcript import TranscriptState
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

__all__ = [
    "MAX_MIX_SKEW_NS",
    "MAX_PENDING_MIX_CHUNKS",
    "MAX_RECORDING_FAILURES",
    "AsrScheduler",
    "ConversationTurn",
    "LiveSession",
    "LiveStatus",
    "SessionResult",
]


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


SchedulerFactory = Callable[
    [CaptureSource, Callable[[TranscriptEvent], None], Callable[[TranscriptEvent], None], Callable[[Exception], None]], AsrScheduler
]

CHECKPOINT_INTERVAL_SECONDS = 2.0
STOP_DRAIN_TIMEOUT_SECONDS = 120.0
"""How long stop() waits for each source's queued decodes.

Stopping drains every queued final (issue #42), but a decode that never
returns — a wedged GPU backend — must not keep the session in STOPPING for
ever: after this the undecoded speech is abandoned and the session still
closes its recordings and writes its exports."""


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
        self._translate = translate or (lambda _ru, en: en)
        self._session_dir = LiveSessionStore(root_dir).create(settings)
        self._log_sink = log
        self._session_log = SessionLog(self._session_dir / "live.log")
        self._journal = EventJournal(self._session_dir / "events.jsonl")
        self._transcript = TranscriptState(self._journal, asr_sample_rate=settings.asr_sample_rate)
        self._conversation = ConversationLog(ConversationJournal(self._session_dir / "conversation.jsonl"))
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
        self._asr_feeds: dict[CaptureSource, AsrFeed] = {}
        self._mix = MixCoordinator(
            enabled=settings.record_mix_audio,
            write_mix=self._recorder.write_mix,
            notify=self._notify,
            log=self.log,
            translate=self._translate,
        )
        self._schedulers: dict[CaptureSource, AsrScheduler] = {}
        self._subscribers: list[Callable[[TranscriptEvent | CaptureEvent | LiveStatus], None]] = []
        self._recording = RecordingGuard(
            self._recorder.write, notify=self._notify, log=self.log, translate=self._translate,
        )
        self._last_checkpoint_at = float("-inf")
        self._lock = RLock()
        self._speakers = SpeakerLabeler(
            diarization_factory or BuiltinDiarizers(),
            asr_sample_rate=settings.asr_sample_rate,
            after_stop_backend=settings.diarization_backend,
            translate=self._translate,
            notify=self._notify,
            lock=self._lock,
        )

    @property
    def session_dir(self) -> Path:
        return self._session_dir

    # Mix state as the session's own attributes, for tests and diagnostics.
    @property
    def _mix_recording_enabled(self) -> bool:
        return self._mix.enabled

    @property
    def _mix_inputs(self) -> dict[CaptureSource, list[PcmChunk]]:
        return self._mix.inputs

    @property
    def _mix_stalled_sources(self) -> set[CaptureSource]:
        return self._mix.stalled_sources

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
            self._mix.start()
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
                self._attempt(errors, "flush mix", self._mix.flush)
                recordings = self._close_recorder(errors)
                recording_files = self._recording_files(recordings)
                self._attempt(errors, "update metadata", self._record_artifacts)
                if self._settings.diarization_mode is DiarizationMode.AFTER_STOP:
                    self._attempt(errors, "diarize", lambda: self._speakers.diarize_recordings(
                        recordings,
                        recording_files,
                        events_for=self._transcript.events_of,
                        publish=self._publish_revision,
                        work_dir=self._session_dir,
                    ))
                self._attempt(errors, "freeze conversation", self._conversation.freeze)
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
        # Called from the assistant's thread while ASR threads publish.
        with self._lock:
            return self._transcript.context()

    def begin_conversation(self, question: str) -> ConversationTurn:
        return self._conversation.begin(question)

    def append_conversation_answer(self, turn_id: str, text: str) -> None:
        self._conversation.append_answer(turn_id, text)

    def finish_conversation(self, turn_id: str, answer: str | None = None, *, status: str = "complete") -> None:
        self._conversation.finish(turn_id, answer, status=status)

    def cancel_conversation(self, turn_id: str) -> None:
        self.finish_conversation(turn_id, "", status="cancelled")

    def clear_conversation(self) -> None:
        self._conversation.clear()

    def conversation(self) -> list[ConversationTurn]:
        return self._conversation.turns()

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
                self._recording.write(aligned)
                self._mix.add(aligned, self._active_sources)
                feed = self._asr_feeds.setdefault(aligned.source, AsrFeed(self._settings.asr_sample_rate))
                derived = feed.derive(aligned)
                if derived is not None:
                    self._schedulers[aligned.source].submit(derived)
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
            self._transcript.record_final(event)
            self._notify(event)
            if self._settings.diarization_mode is not DiarizationMode.LIVE_ESTIMATE:
                return
            recent = self._transcript.recent(event)
        # Creating the diarizer loads a model. Under the session lock that
        # stalled _on_chunk for every source, and the native client's backlog
        # overflowed and dropped audio meanwhile.
        estimates = self._speakers.estimate_live(event.source, recent)
        with self._lock:
            for revised in self._speakers.revised(recent, estimates):
                self._publish_revision(revised)

    def _on_partial(self, event: TranscriptEvent) -> None:
        with self._lock:
            if self._transcript.accept_partial(event):
                self._notify(event)

    def _publish_revision(self, event: TranscriptEvent) -> None:
        self._transcript.record_revision(event)
        self._notify(event)

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
