"""JSONL-facing live session controller; audio is pushed by the native client."""

from __future__ import annotations

import base64
import binascii
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from src.live.asr import LiveAsrScheduler
from src.live.asr_backend import LazyModelBackend
from src.live.capture.push import PushCaptureAdapter
from src.live.exports import ExportSelection
from src.live.session import LiveSession, LiveStatus
from src.live.types import (
    CaptureEvent,
    CaptureEventKind,
    CaptureSource,
    DiarizationMode,
    LiveSettings,
    TranscriptEvent,
)
from src.services import llm_service

_NOT_RUNNING = "No live session is running"


class LiveWorkerService:
    """One live session per worker, driven entirely by JSONL commands."""

    def __init__(
        self,
        emit: Callable[..., None],
        *,
        session_factory: Callable[..., LiveSession] = LiveSession,
        scheduler_factory: Callable[..., Any] | None = None,
        model_loader_factory: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self._emit = emit
        self._session_factory = session_factory
        self._scheduler_factory = scheduler_factory
        self._model_loader_factory = model_loader_factory or self._default_model_loader
        self._session: LiveSession | None = None
        self._adapters: dict[CaptureSource, PushCaptureAdapter] = {}
        self._sample_rate = 16_000
        self._run_provider = llm_service.run_provider
        self._thread_factory: Callable[..., Any] = threading.Thread
        self._ask_cancel = threading.Event()
        self._ask_thread: Any = None

    @property
    def session(self) -> LiveSession | None:
        return self._session

    def is_running(self) -> bool:
        return self._session is not None

    # -- session commands ---------------------------------------------------

    def start(self, command: dict[str, Any]) -> None:
        if self._session is not None:
            self._emit("error", message="Processing is already running")
            return
        root = Path(str(command.get("session_root") or ""))
        if not root.is_dir():
            self._emit("error", message="session_root must be an existing directory")
            return
        raw_sources = command.get("sources") or []
        if not isinstance(raw_sources, list) or not raw_sources:
            self._emit("error", message="sources must contain mic and/or system")
            return
        sources: list[CaptureSource] = []
        for value in raw_sources:
            try:
                sources.append(CaptureSource(str(value)))
            except ValueError:
                self._emit("error", message=f"Unknown live source: {value!r}")
                return
        try:
            mode = DiarizationMode(str(command.get("diarization_mode") or "off"))
        except ValueError:
            self._emit("error", message=f"Unknown diarization mode: {command.get('diarization_mode')!r}")
            return
        self._sample_rate = int(command.get("sample_rate") or 16_000)
        record_mic = bool(command.get("record_mic", True))
        record_system = bool(command.get("record_system", True))
        settings = LiveSettings(
            diarization_mode=mode,
            source_sample_rate=self._sample_rate,
            asr_sample_rate=16_000,
            record_mic_audio=CaptureSource.MIC in sources and record_mic,
            record_system_audio=CaptureSource.SYSTEM in sources and record_system,
            record_source_audio=record_mic or record_system,
            record_mix_audio=set(sources) == {CaptureSource.MIC, CaptureSource.SYSTEM},
        )
        exports_raw = command.get("exports") or {"txt": True}
        if not isinstance(exports_raw, dict):
            self._emit("error", message="exports must be an object")
            return
        try:
            exports = ExportSelection(**{k: v for k, v in exports_raw.items() if k in ExportSelection.__dataclass_fields__})
        except TypeError as exc:
            self._emit("error", message=f"Invalid exports: {exc}")
            return
        adapters = {source: PushCaptureAdapter(source, self._sample_rate) for source in sources}
        scheduler_factory = self._scheduler_factory or self._make_scheduler_factory(command)
        backend = str(command.get("diarization_backend") or "pyannote")
        try:
            session = self._session_factory(
                root, settings, adapters,
                scheduler_factory=scheduler_factory,
                export_selection=exports,
                diarization_factory=lambda _requested, backend=backend: self._create_diarizer(backend),
                log=lambda message: self._emit("log", message=message),
            )
            session.subscribe(self._on_update)
            session.start()
        except Exception as exc:
            self._emit("error", message=f"Could not start live session: {exc}")
            return
        self._adapters = adapters
        self._session = session

    def audio(self, command: dict[str, Any]) -> None:
        adapter = self._adapter_for(command)
        if adapter is None:
            return
        try:
            raw = base64.b64decode(str(command.get("pcm") or ""), validate=True)
            if len(raw) % 2:
                raise ValueError("odd byte count")
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        except (binascii.Error, ValueError):
            self._emit("error", message="live_audio pcm is not valid base64 int16")
            return
        try:
            seq = int(command.get("seq", 0))
            offset = int(command.get("sample_offset", 0))
            timestamp_ns = int(command.get("timestamp_ns", 0))
        except (TypeError, ValueError):
            self._emit("error", message="live_audio seq/sample_offset/timestamp_ns must be integers")
            return
        adapter.push(seq, offset, timestamp_ns, samples[:, None])

    def capture_event(self, command: dict[str, Any]) -> None:
        adapter = self._adapter_for(command)
        if adapter is None:
            return
        try:
            kind = CaptureEventKind(str(command.get("kind") or ""))
        except ValueError:
            self._emit("error", message=f"Unknown capture event kind: {command.get('kind')!r}")
            return
        adapter.event(kind, str(command.get("detail") or ""))

    def pause(self) -> None:
        if self._session is None:
            self._emit("error", message=_NOT_RUNNING)
            return
        self._session.pause()

    def resume(self) -> None:
        if self._session is None:
            self._emit("error", message=_NOT_RUNNING)
            return
        self._session.resume()

    def stop(self) -> None:
        session = self._session
        if session is None:
            self._emit("error", message=_NOT_RUNNING)
            return
        try:
            result = session.stop()
        except Exception as exc:
            self._emit("live_stopped", session_dir=str(session.session_dir), saved_files=[], recordings={}, message=str(exc))
        else:
            self._emit(
                "live_stopped",
                session_dir=str(result.session_dir),
                saved_files=[str(path) for path in result.exports],
                recordings={source.value: str(path) for source, path in result.recordings.items()},
            )
        finally:
            self._session = None
            self._adapters = {}

    # -- assistant questions ------------------------------------------------

    def ask(self, command: dict[str, Any]) -> None:
        session = self._session
        if session is None:
            self._emit("error", message=_NOT_RUNNING)
            return
        question = str(command.get("question") or "").strip()
        if not question:
            self._emit("error", message="Question is required")
            return
        transcript = session.ask_context()
        if not transcript:
            self._emit("error", message="No final transcript events are available yet")
            return
        settings = command.get("settings")
        if not isinstance(settings, dict):
            self._emit("error", message="LLM settings are required")
            return
        if self._ask_thread is not None and self._ask_thread.is_alive():
            self._emit("error", message="An assistant question is already running")
            return
        turn = session.begin_conversation(question)
        self._ask_cancel = threading.Event()
        self._ask_thread = self._thread_factory(
            target=self._answer, args=(session, turn.id, transcript, question, settings, self._ask_cancel), daemon=True,
        )
        self._ask_thread.start()

    def ask_cancel(self) -> None:
        if self._ask_thread is None or not self._ask_thread.is_alive():
            self._emit("error", message="No assistant question is running")
            return
        self._ask_cancel.set()

    def _answer(self, session, turn_id, transcript, question, settings, cancel) -> None:
        def stream(chunk: str) -> None:
            session.append_conversation_answer(turn_id, chunk)
            self._emit("live_answer_chunk", turn_id=turn_id, text=chunk)

        try:
            answer = self._run_provider(
                settings, transcript, question, provider=str(settings.get("provider") or "API"),
                strict_empty_cli=True, on_stream_chunk=stream, cancel_check=cancel.is_set,
            )
        except Exception as exc:
            if cancel.is_set():
                session.cancel_conversation(turn_id)
                self._emit("live_answer", turn_id=turn_id, status="cancelled", text="")
            else:
                session.finish_conversation(turn_id, "", status="error")
                self._emit("live_answer", turn_id=turn_id, status="error", text=str(exc))
            return
        if cancel.is_set():
            session.cancel_conversation(turn_id)
            self._emit("live_answer", turn_id=turn_id, status="cancelled", text="")
            return
        session.finish_conversation(turn_id, answer)
        self._emit("live_answer", turn_id=turn_id, status="complete", text=answer)

    # -- helpers ------------------------------------------------------------

    def _adapter_for(self, command: dict[str, Any]) -> PushCaptureAdapter | None:
        if self._session is None:
            self._emit("error", message=_NOT_RUNNING)
            return None
        value = str(command.get("source") or "")
        try:
            source = CaptureSource(value)
        except ValueError:
            self._emit("error", message=f"Unknown live source: {value!r}")
            return None
        adapter = self._adapters.get(source)
        if adapter is None:
            self._emit("error", message=f"Unknown live source: {value!r}")
        return adapter

    def _on_update(self, value: TranscriptEvent | CaptureEvent | LiveStatus) -> None:
        if isinstance(value, LiveStatus):
            self._emit(
                "live_status",
                state=value.state.value,
                active_sources=sorted(s.value for s in value.active_sources),
                failed_sources=sorted(s.value for s in value.failed_sources),
            )
        elif isinstance(value, CaptureEvent):
            self._emit("live_capture_event", source=value.source.value, kind=value.kind.value, detail=value.detail)
        elif isinstance(value, TranscriptEvent):
            self._emit(
                "live_final" if value.status == "final" else "live_partial",
                event_id=value.event_id,
                revision=value.revision,
                source=value.source.value,
                sample_start=value.sample_start,
                sample_end=value.sample_end,
                text=value.text,
                speaker=value.speaker,
                paragraph_break_after=value.paragraph_break_after,
            )

    def _make_scheduler_factory(self, command: dict[str, Any]):
        loader = self._model_loader_factory(command)
        backend = LazyModelBackend(loader, "Could not load the recognition model")

        def factory(source, on_final, on_partial, on_error):
            return LiveAsrScheduler(backend, on_final=on_final, on_partial=on_partial, on_error=on_error)

        return factory

    @staticmethod
    def _default_model_loader(command: dict[str, Any]):
        from src.core.model_loader import ModelLoader

        return ModelLoader(
            requested_backend=str(command.get("backend") or "auto"),
            model_revision=str(command.get("model") or "v3_e2e_rnnt"),
            onnx_provider=str(command.get("onnx_provider") or "auto"),
        )

    @staticmethod
    def _create_diarizer(backend: str):
        from src.core.diarization.factory import create_diarization_backend

        return create_diarization_backend(backend)
