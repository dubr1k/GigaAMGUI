"""Source-isolated live capture lifecycle coordinator."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Protocol

from src.core.asr.types import normalize_window_audio

from .capture.base import CaptureAdapter
from .diarization import LIVE_ESTIMATE_STABILIZATION_HORIZON_SECONDS, label_event
from .exports import ExportSelection, export_session
from .journal import ConversationJournal, EventJournal, LiveSessionStore
from .recorder import SessionRecorder
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

    def close(self) -> None: ...


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
MAX_PENDING_MIX_CHUNKS = 100


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
    ) -> None:
        self._settings = settings
        self._adapters = dict(adapters)
        self._scheduler_factory = scheduler_factory
        self._export_selection = replace(
            export_selection or ExportSelection(),
            sample_rate=settings.asr_sample_rate,
        )
        self._recorder_factory = recorder_factory
        self._diarization_factory = diarization_factory
        self._translate = translate or (lambda _ru, en: en)
        self._session_dir = LiveSessionStore(root_dir).create(settings)
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
        self._active_sources: set[CaptureSource] = set()
        self._failed_sources: set[CaptureSource] = set()
        self._timelines: dict[CaptureSource, SourceTimeline] = {}
        self._mix_inputs: dict[CaptureSource, list[PcmChunk]] = {}
        self._mix_timestamp_origins: dict[CaptureSource, int] = {}
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
        self._lock = RLock()

    def start(self) -> None:
        with self._lock:
            if self._state is not CaptureState.IDLE:
                raise RuntimeError("session has already started")
            self._state = CaptureState.STARTING
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
        with self._lock:
            if self._state in {CaptureState.STOPPED, CaptureState.IDLE}:
                raise RuntimeError("session is not running")
            self._state = CaptureState.STOPPING
            for source in self._active_sources:
                self._adapters[source].stop()
            for scheduler in self._schedulers.values():
                scheduler.flush()
                scheduler.close()
            self._flush_mix_inputs()
            recordings = self._recorder.close()
            artifacts = getattr(self._recorder, "artifacts", None)
            if callable(artifacts):
                LiveSessionStore(self._session_dir.parent).update_metadata(
                    self._session_dir,
                    recordings=artifacts(),
                )
            if self._settings.diarization_mode is DiarizationMode.AFTER_STOP:
                self._diarize_recordings(recordings)
            self._freeze_conversation()
            exports = export_session(self._session_dir, self._journal.latest_events(), self._export_selection)
            self._active_sources.clear()
            self._state = CaptureState.STOPPED
            self._notify_status()
            return SessionResult(self._session_dir, recordings, exports)

    def status(self) -> LiveStatus:
        with self._lock:
            return LiveStatus(self._state, set(self._active_sources), set(self._failed_sources))

    def ask_context(self) -> str:
        final_text = "\n".join(
            f"[{datetime.fromtimestamp(event.timestamp_ns / 1_000_000_000, timezone.utc).isoformat()}] "
            f"{event.source_label}{f' / {event.speaker}' if event.speaker else ''}: {event.text}"
            for event in self._journal.latest_events()
            if event.status == "final"
        )
        drafts = "\n".join(
            f"[{source.value.upper()} draft] {event.text}"
            for source, event in self._partials.items()
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
                self._recorder.write(aligned)
                if self._mix_recording_enabled:
                    pending = self._mix_inputs.setdefault(aligned.source, [])
                    pending.append(self._normalize_mix_timestamp(aligned))
                    if len(pending) > MAX_PENDING_MIX_CHUNKS:
                        self._disable_mix_recording(
                            aligned.source,
                            aligned.timestamp_ns,
                            "pending input frame limit exceeded",
                        )
                    else:
                        self._write_ready_mixes()
                audio = normalize_window_audio(aligned.frames[:, 0], aligned.sample_rate, self._settings.asr_sample_rate)
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
            LiveSessionStore(self._session_dir.parent).write_checkpoint(
                self._session_dir,
                {"active_sources": sorted(source.value for source in self._active_sources)},
            )

    def _normalize_mix_timestamp(self, chunk: PcmChunk) -> PcmChunk:
        if self._mix_session_origin_ns is None:
            self._mix_session_origin_ns = chunk.timestamp_ns
        source_origin_ns = self._mix_timestamp_origins.setdefault(chunk.source, chunk.timestamp_ns)
        return replace(
            chunk,
            timestamp_ns=self._mix_session_origin_ns + chunk.timestamp_ns - source_origin_ns,
        )

    def _write_ready_mixes(self) -> None:
        if not self._mix_recording_enabled:
            return
        while self._active_sources and all(self._mix_inputs.get(source) for source in self._active_sources):
            heads = {source: self._mix_inputs[source][0] for source in self._active_sources}
            earliest = min(heads.values(), key=lambda chunk: chunk.timestamp_ns)
            latest_timestamp_ns = max(chunk.timestamp_ns for chunk in heads.values())
            if latest_timestamp_ns - earliest.timestamp_ns > MAX_MIX_SKEW_NS:
                self._disable_mix_recording(
                    earliest.source,
                    earliest.timestamp_ns,
                    "timestamp skew exceeds 1.000s",
                )
                return
            inputs = {source: self._mix_inputs[source].pop(0) for source in self._active_sources}
            self._write_mix(inputs)

    def _flush_mix_inputs(self) -> None:
        if not self._mix_recording_enabled:
            self._mix_inputs.clear()
            return
        pending = [chunk for chunks in self._mix_inputs.values() for chunk in chunks]
        self._mix_inputs.clear()
        for chunk in sorted(pending, key=lambda item: item.timestamp_ns):
            self._write_mix({chunk.source: chunk})

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

    def _on_event(self, event: CaptureEvent) -> None:
        with self._lock:
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
            if self._settings.diarization_mode is DiarizationMode.LIVE_ESTIMATE:
                for revised in self._estimate_live_speakers(finalized):
                    self._record_finalized(revised)
                    self._journal.append(revised)
                    self._notify(revised)

    def _on_partial(self, event: TranscriptEvent) -> None:
        with self._lock:
            if event.revision <= self._finalized_revisions.get((event.source, event.event_id), -1):
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

    def _estimate_live_speakers(self, event: TranscriptEvent) -> list[TranscriptEvent]:
        diarizer = self._live_diarizers.get(event.source)
        if diarizer is None and event.source not in self._live_diarization_unavailable:
            try:
                diarizer = self._create_diarizer("sortformer")
                self._live_diarizers[event.source] = diarizer
            except Exception as exc:
                self._report_live_diarization_unavailable(event.source, str(exc))
                return []
        estimate = getattr(diarizer, "estimate_events", None)
        if not callable(estimate):
            self._report_live_diarization_unavailable(
                event.source,
                "Live Sortformer estimate unavailable; use After stop for offline speaker labels. "
                "Retaining source labels.",
            )
            return []
        horizon_samples = LIVE_ESTIMATE_STABILIZATION_HORIZON_SECONDS * self._settings.asr_sample_rate
        recent = [
            item
            for item in self._journal.latest_events()
            if item.source is event.source and event.sample_end - item.sample_end <= horizon_samples
        ]
        try:
            estimates = estimate(
                recent,
                stabilization_horizon_seconds=LIVE_ESTIMATE_STABILIZATION_HORIZON_SECONDS,
            )
        except Exception as exc:
            self._report_live_diarization_unavailable(event.source, str(exc))
            return []
        return self._revised_speakers(recent, estimates)

    def _diarize_recordings(self, recordings: dict[CaptureSource, Path]) -> None:
        for source, path in recordings.items():
            try:
                diarizer = self._create_diarizer("onnx")
                segments = diarizer.diarize(str(path))
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

    def _create_diarizer(self, backend: str):
        if self._diarization_factory is not None:
            return self._diarization_factory(backend)
        from src.core.diarization.factory import create_diarization_backend

        return create_diarization_backend(backend)

    def _segment_speakers(self, events, segments) -> dict[str, str]:
        speakers = {}
        for event in events:
            start = event.sample_start / self._settings.asr_sample_rate
            end = event.sample_end / self._settings.asr_sample_rate
            overlaps = [
                (max(0.0, min(end, segment.end) - max(start, segment.start)), segment.speaker)
                for segment in segments
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
        self._notify(CaptureEvent(CaptureEventKind.STATUS, source, 0, 0, str(error)))

    def _mark_failed(self, source: CaptureSource, detail: str) -> None:
        self._active_sources.discard(source)
        self._failed_sources.add(source)
        if not self._active_sources and self._state is not CaptureState.STARTING:
            self._state = CaptureState.FAILED
        self._notify(CaptureEvent(CaptureEventKind.STATUS, source, 0, 0, detail))

    def _notify_status(self) -> None:
        self._notify(self.status())

    def _notify(self, value: TranscriptEvent | CaptureEvent | LiveStatus) -> None:
        for callback in tuple(self._subscribers):
            callback(value)
