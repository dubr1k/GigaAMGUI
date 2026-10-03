"""Оверлей Live и вопросы ассистенту по идущей сессии.

Оверлей — отдельное окно поверх всех с текстом расшифровки и полем вопроса.
Вопрос уходит выбранному LLM-провайдеру с контекстом сессии в фоне; ответ
и его поток приходят сигналом live_answer и пишутся в разговор сессии.

Mixin: методы работают со `self` главного окна.
"""
from __future__ import annotations

import re
import threading

from ..live.types import CaptureState, TranscriptEvent
from .live_overlay import LiveOverlay


class LiveAssistantMixin:
    def _show_live_overlay(self) -> None:
        if self.live_overlay is None:
            self.live_overlay = LiveOverlay(self)
            self.live_overlay.question_submitted.connect(self._answer_live_question)
            self.live_overlay.cancel_requested.connect(self._cancel_live_question)
            self.live_overlay.visibility_changed.connect(self._on_live_overlay_visibility)
        if self.live_session is not None and callable(getattr(self.live_session, "conversation", None)):
            self.live_overlay.set_conversation(self.live_session.conversation())
        self.live_overlay.show()
        self.live_overlay.raise_()

    def _hide_live_overlay(self) -> None:
        if self.live_overlay is not None:
            self.live_overlay.hide()

    def _toggle_live_overlay(self) -> None:
        """Mirror the frameless overlay's own close affordances on the tab button."""
        if self.btn_live_overlay.isChecked():
            self._show_live_overlay()
        else:
            self._hide_live_overlay()

    def _on_live_overlay_visibility(self, visible: bool) -> None:
        if self.btn_live_overlay.isChecked() != visible:
            blocked = self.btn_live_overlay.blockSignals(True)
            self.btn_live_overlay.setChecked(visible)
            self.btn_live_overlay.blockSignals(blocked)
        self._live_settings["overlay_visible"] = visible
        self.user_settings.set_value("live_settings", self._live_settings)

    def _update_live_overlay(self, event) -> None:
        if self.live_overlay is not None and isinstance(event, TranscriptEvent):
            self.live_overlay.update_transcript(event)

    def _answer_live_question(self, question: str) -> None:
        if self.live_session is None:
            self._update_live_answer(
                "error",
                self._t(
                    "Сначала запустите live-сессию, прежде чем задавать вопрос ассистенту.",
                    "Start a live session before asking the assistant.",
                ),
            )
            return
        transcript = self.live_session.ask_context()
        if not transcript:
            self._update_live_answer(
                "error",
                self._t(
                    "Пока нет финальных событий расшифровки.",
                    "No final transcript events are available yet.",
                ),
            )
            return
        try:
            llm_settings = self._collect_llm_settings()
        except ValueError as exc:
            self._update_live_answer(
                "error",
                self._t(f"LLM не настроена: {exc}", f"LLM is not configured: {exc}"),
            )
            return
        stopped = self._t(
            "Live-сессия уже остановлена: вопросы ассистенту принимаются во время записи.",
            "The live session has stopped: the assistant takes questions while recording.",
        )
        if self.live_session.status().state in (CaptureState.STOPPING, CaptureState.STOPPED):
            self._update_live_answer("error", stopped)
            return
        try:
            turn = self.live_session.begin_conversation(question)
        except RuntimeError:
            # stop() замораживает разговор; исключение из Qt-слота роняет приложение.
            self._update_live_answer("error", stopped)
            return
        self._live_conversation_id = turn.id
        self._sync_live_conversation()
        cancel_event = threading.Event()
        self._live_llm_cancel_event = cancel_event
        threading.Thread(
            target=self._run_live_question,
            args=(llm_settings, transcript, question, cancel_event),
            daemon=True,
        ).start()

    def _run_live_question(
        self, llm_settings: dict, transcript: str, question: str, cancel_event: threading.Event | None = None,
    ) -> None:
        cancel_event = cancel_event or threading.Event()
        try:
            answer = self._run_llm_provider(
                llm_settings,
                transcript,
                question,
                on_stream_chunk=lambda chunk: self.signals.live_answer.emit("chunk", chunk),
                cancel_check=cancel_event.is_set,
            )
        except Exception as exc:
            if cancel_event.is_set():
                return
            detail = self._live_llm_error_detail(str(exc), llm_settings)
            self.signals.live_answer.emit(
                "error",
                self._t(f"Ошибка LLM: {detail}", f"LLM error: {detail}"),
            )
        else:
            if not cancel_event.is_set():
                self.signals.live_answer.emit("answer", answer)
        finally:
            if self._live_llm_cancel_event is cancel_event:
                self._live_llm_cancel_event = None

    def _cancel_live_question(self) -> None:
        if self._live_llm_cancel_event is not None:
            self._live_llm_cancel_event.set()
        if self.live_session is not None and self._live_conversation_id is not None:
            self.live_session.cancel_conversation(self._live_conversation_id)
            self._live_conversation_id = None
            self._sync_live_conversation()

    def _live_llm_error_detail(self, error: str, llm_settings: dict) -> str:
        diagnostic = (error or "").strip()
        api_key = llm_settings.get("api_key", "")
        if api_key:
            diagnostic = diagnostic.replace(api_key, "[redacted]")
        diagnostic = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[redacted]", diagnostic)
        compact = self._compact_llm_error(diagnostic)
        normalized = " ".join(diagnostic.split())
        if normalized and normalized not in compact:
            return f"{compact}: {normalized[:300]}"
        return compact

    def _update_live_answer(self, status: str, text: str) -> None:
        if self.live_session is not None and self._live_conversation_id is not None:
            try:
                if status == "chunk":
                    self.live_session.append_conversation_answer(self._live_conversation_id, text)
                else:
                    self.live_session.finish_conversation(
                        self._live_conversation_id,
                        text,
                        status="error" if status == "error" else "complete",
                    )
                    self._live_conversation_id = None
            except RuntimeError:
                # The session stopped and froze its conversation while the
                # answer was in flight. An exception escaping a Qt slot aborts
                # the whole application, so the late answer is dropped instead.
                self._live_conversation_id = None
            self._sync_live_conversation()
            return
        if self.live_overlay is not None:
            if status == "chunk":
                self.live_overlay.append_answer(text)
            else:
                self.live_overlay.set_answer(text)
                self.live_overlay.finish_generation()

    def _sync_live_conversation(self) -> None:
        if self.live_overlay is not None and self.live_session is not None:
            self.live_overlay.set_conversation(self.live_session.conversation())
            if self._live_conversation_id is None:
                self.live_overlay.finish_generation()
