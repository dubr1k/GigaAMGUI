"""Live-tab settings and Qt-facing capture session controller."""

from __future__ import annotations

import re
import sys
import threading
import time
from pathlib import Path

from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QApplication, QFileDialog

from ..live.asr import LiveAsrScheduler
from ..live.capture.factory import CaptureUnavailable, create_capture_adapter
from ..live.exports import ExportSelection
from ..live.session import LiveSession, LiveStatus
from ..live.types import (
    CaptureEvent,
    CaptureEventKind,
    CaptureSource,
    CaptureState,
    DiarizationMode,
    LiveSettings,
    TranscriptEvent,
)
from .live_overlay import LiveOverlay
from .live_transcript import LiveTranscriptPresenter


class _LiveModelBackend:
    """Lazily load the selected model on the scheduler worker, not the Qt thread."""

    def __init__(self, model_loader, load_error: str) -> None:
        self._model_loader = model_loader
        self._load_error = load_error

    def transcribe_window(self, audio, sample_rate: int, offset_samples: int):
        if not self._model_loader.is_loaded() and not self._model_loader.load_model():
            detail = self._model_loader.diagnostics().get("error")
            raise RuntimeError(f"{self._load_error}: {detail or 'unknown error'}")
        return self._model_loader.transcribe_window(audio, sample_rate, offset_samples)


class LiveMixin:
    def _init_live_state(self) -> None:
        self.live_session = None
        self.live_overlay = None
        self._live_settings = self.user_settings.get_value("live_settings", {}) or {}
        self._live_partial_range = None
        self._live_partial_text = ""
        self._live_transcript_presenter = LiveTranscriptPresenter()
        self._live_capture_status_times: dict[tuple[CaptureSource, str], float] = {}
        self._live_llm_cancel_event: threading.Event | None = None
        self._live_conversation_id: str | None = None
        self._live_stop_thread: threading.Thread | None = None

    def _restore_live_settings(self) -> None:
        settings = self._live_settings
        self._set_live_combo_value(self.combo_live_source, settings.get("source", "mic"))
        self._set_live_combo_value(self.combo_live_mic_device, settings.get("mic_device_id"))
        self._set_live_combo_value(self.combo_live_system_device, settings.get("system_device_id"))
        self.cb_live_mic_audio.setChecked(bool(settings.get("record_mic_audio", True)))
        self.cb_live_system_audio.setChecked(bool(settings.get("record_system_audio", True)))
        self._set_live_combo_value(self.combo_live_diarization, settings.get("diarization_mode", "off"))
        self.cb_live_export_txt.setChecked(bool(settings.get("export_txt", True)))
        self.cb_live_export_txt_timecodes.setChecked(bool(settings.get("export_txt_timecodes", True)))
        self.cb_live_export_txt_diarize.setChecked(bool(settings.get("export_txt_diarize", False)))
        self.cb_live_export_txt_diarize_timecodes.setChecked(bool(settings.get("export_txt_diarize_timecodes", False)))
        self.cb_live_export_md.setChecked(bool(settings.get("export_md", False)))
        self.cb_live_export_srt.setChecked(bool(settings.get("export_srt", False)))
        self.cb_live_export_vtt.setChecked(bool(settings.get("export_vtt", False)))
        self.cb_live_subtitle_sentence_split.setChecked(bool(settings.get("subtitle_sentence_split", True)))
        self.spin_live_subtitle_max_lines.setValue(int(settings.get("subtitle_max_line_count", 2)))
        self.spin_live_subtitle_max_width.setValue(int(settings.get("subtitle_max_line_width", 64)))
        self.spin_live_gain.setValue(float(settings.get("gain", 1.0)))
        self.live_output_dir.setText(str(settings.get("output_dir", self.output_dir)))
        if bool(settings.get("overlay_visible", False)):
            self.btn_live_overlay.setChecked(True)
            self._show_live_overlay()
        self._refresh_live_devices()
        self._update_live_source_controls()
        self._update_live_export_controls()

    @staticmethod
    def _set_live_combo_value(combo, value) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _save_live_settings(self) -> None:
        self._live_settings = {
            "source": self.combo_live_source.currentData(),
            "mic_device_id": self.combo_live_mic_device.currentData(),
            "system_device_id": self.combo_live_system_device.currentData(),
            "record_mic_audio": self.cb_live_mic_audio.isChecked(),
            "record_system_audio": self.cb_live_system_audio.isChecked(),
            "export_txt": self.cb_live_export_txt.isChecked(),
            "export_txt_timecodes": self.cb_live_export_txt_timecodes.isChecked(),
            "export_txt_diarize": self.cb_live_export_txt_diarize.isChecked(),
            "export_txt_diarize_timecodes": self.cb_live_export_txt_diarize_timecodes.isChecked(),
            "export_md": self.cb_live_export_md.isChecked(),
            "export_srt": self.cb_live_export_srt.isChecked(),
            "export_vtt": self.cb_live_export_vtt.isChecked(),
            "subtitle_sentence_split": self.cb_live_subtitle_sentence_split.isChecked(),
            "subtitle_max_line_count": self.spin_live_subtitle_max_lines.value(),
            "subtitle_max_line_width": self.spin_live_subtitle_max_width.value(),
            "overlay_visible": bool(self.btn_live_overlay.isChecked()),
            "diarization_mode": self.combo_live_diarization.currentData(),
            "gain": self.spin_live_gain.value(),
            "output_dir": self.live_output_dir.text().strip(),
        }
        self.user_settings.set_value("live_settings", self._live_settings)

    def _selected_live_sources(self) -> set[CaptureSource]:
        source = self.combo_live_source.currentData()
        if source == "mic":
            return {CaptureSource.MIC}
        if source == "system":
            return {CaptureSource.SYSTEM}
        return {CaptureSource.MIC, CaptureSource.SYSTEM}

    def _update_live_source_controls(self) -> None:
        sources = self._selected_live_sources()
        self.cb_live_mic_audio.setEnabled(CaptureSource.MIC in sources)
        self.combo_live_mic_device.setEnabled(CaptureSource.MIC in sources)
        self.cb_live_system_audio.setEnabled(CaptureSource.SYSTEM in sources)
        self.combo_live_system_device.setEnabled(CaptureSource.SYSTEM in sources)

    def _update_live_export_controls(self, *_args) -> None:
        diarization_enabled = self.combo_live_diarization.currentData() != DiarizationMode.OFF.value
        for checkbox in (
            self.cb_live_export_txt_diarize,
            self.cb_live_export_txt_diarize_timecodes,
        ):
            if not diarization_enabled:
                signals_were_blocked = checkbox.blockSignals(True)
                checkbox.setChecked(False)
                checkbox.blockSignals(signals_were_blocked)
            checkbox.setEnabled(diarization_enabled)
        subtitles_enabled = self.cb_live_export_srt.isChecked() or self.cb_live_export_vtt.isChecked()
        for widget in (
            self.cb_live_subtitle_sentence_split,
            self.lbl_live_subtitle_max_lines,
            self.spin_live_subtitle_max_lines,
            self.lbl_live_subtitle_max_width,
            self.spin_live_subtitle_max_width,
        ):
            widget.setEnabled(subtitles_enabled)

    def _refresh_live_devices(self) -> None:
        selected = {
            CaptureSource.MIC: self.combo_live_mic_device.currentData(),
            CaptureSource.SYSTEM: self.combo_live_system_device.currentData(),
        }
        for source, combo in (
            (CaptureSource.MIC, self.combo_live_mic_device),
            (CaptureSource.SYSTEM, self.combo_live_system_device),
        ):
            combo.clear()
            try:
                devices = self._probe_devices(source)
            except Exception:
                devices = []
            for device in devices:
                combo.addItem(device.name, device.id)
            index = combo.findData(selected[source])
            if index < 0:
                index = next(
                    (item for item in range(combo.count()) if devices[item].is_default),
                    0,
                )
            if index >= 0 and combo.count():
                combo.setCurrentIndex(index)

    @staticmethod
    def _probe_devices(source: CaptureSource) -> list:
        """Enumerate devices and free the native handle straight away.

        Each probe adapter owns a PyAudio/WASAPI instance, so leaving them to
        the garbage collector leaked one native handle per device refresh.
        """
        probe = create_capture_adapter(sys.platform, source)
        try:
            return probe.devices()
        finally:
            release = getattr(probe, "release", None)
            if callable(release):
                release()

    def _select_live_output_folder(self) -> None:
        initial_dir = self.live_output_dir.text().strip() or self.output_dir or str(Path.home())
        selected = QFileDialog.getExistingDirectory(
            self,
            self._t("Выберите папку сессий", "Select session folder"),
            initial_dir,
        )
        if selected:
            self.live_output_dir.setText(selected)

    def _update_live_output_folder_label(self, path: str) -> None:
        self.lbl_live_output_folder.setText(
            path or self._t("Папка не выбрана", "Folder not selected")
        )

    def _start_live_session(self) -> None:
        if self.live_session is not None and self.live_session.status().state is CaptureState.PAUSED:
            self.live_session.resume()
            return
        output_dir = self.live_output_dir.text().strip()
        if not output_dir or not Path(output_dir).is_dir():
            self.lbl_live_status.setText(
                self._t("Выберите существующую папку сессий", "Select an existing session folder")
            )
            return
        self._clear_live_problem()
        if not self._preload_live_model():
            return
        self._save_live_settings()
        sources = self._selected_live_sources()
        settings = LiveSettings(
            mic_device_id=self.combo_live_mic_device.currentData(),
            system_device_id=self.combo_live_system_device.currentData(),
            diarization_mode=DiarizationMode(self.combo_live_diarization.currentData()),
            record_mic_audio=CaptureSource.MIC in sources and self.cb_live_mic_audio.isChecked(),
            record_system_audio=CaptureSource.SYSTEM in sources and self.cb_live_system_audio.isChecked(),
            record_source_audio=any(
                checkbox.isChecked()
                for checkbox in (self.cb_live_mic_audio, self.cb_live_system_audio)
            ),
            record_mix_audio=sources == {CaptureSource.MIC, CaptureSource.SYSTEM},
        )
        try:
            adapters = {
                source: create_capture_adapter(
                    sys.platform,
                    source,
                    settings.mic_device_id if source is CaptureSource.MIC else settings.system_device_id,
                )
                for source in sources
            }
        except CaptureUnavailable as exc:
            self.lbl_live_status.setText(str(exc))
            self._report_live_problem(str(exc))
            return
        self.live_session = LiveSession(
            Path(output_dir),
            settings,
            adapters,
            scheduler_factory=lambda source, on_final, on_partial, on_error: LiveAsrScheduler(
                _LiveModelBackend(
                    self.model_loader,
                    self._t("Не удалось загрузить модель распознавания", "Could not load recognition model"),
                ),
                on_final=on_final,
                on_partial=on_partial,
                on_error=on_error,
            ),
            export_selection=ExportSelection(
                txt=self.cb_live_export_txt.isChecked(),
                txt_timecodes=self.cb_live_export_txt_timecodes.isChecked(),
                txt_diarize=self.cb_live_export_txt_diarize.isChecked(),
                txt_diarize_timecodes=self.cb_live_export_txt_diarize_timecodes.isChecked(),
                md=self.cb_live_export_md.isChecked(),
                srt=self.cb_live_export_srt.isChecked(),
                vtt=self.cb_live_export_vtt.isChecked(),
                sentence_split=self.cb_live_subtitle_sentence_split.isChecked(),
                max_line_count=self.spin_live_subtitle_max_lines.value(),
                max_line_width=self.spin_live_subtitle_max_width.value(),
                sample_rate=settings.asr_sample_rate,
            ),
            translate=self._t,
            log=self._log_live,
        )
        self.live_session.subscribe(self._on_live_session_update)
        self.live_session.start()

    def _log_live(self, message: str) -> None:
        """Route live-path diagnostics into the shared processing log tab."""
        self.log(f"[live] {message}")

    def _preload_live_model(self) -> bool:
        """Fail loudly before capture starts instead of decoding into a void."""
        if self.model_loader.is_loaded():
            return True
        self.lbl_live_status.setText(
            self._t("Загрузка модели распознавания…", "Loading the recognition model…")
        )
        QApplication.processEvents()
        if self.model_loader.load_model(logger=self.log):
            return True
        detail = self.model_loader.diagnostics().get("error") or self._t("неизвестная ошибка", "unknown error")
        message = self._t(
            f"Не удалось загрузить модель распознавания: {detail}",
            f"Could not load the recognition model: {detail}",
        )
        self.lbl_live_status.setText(self._t("Готово к записи", "Ready for live capture"))
        self._report_live_problem(message)
        return False

    def _report_live_problem(self, message: str) -> None:
        self.lbl_live_problem.setText(message)
        self.lbl_live_problem.show()
        self.log(f"[live] {message}")

    def _clear_live_problem(self) -> None:
        self.lbl_live_problem.clear()
        self.lbl_live_problem.hide()

    def _pause_live_session(self) -> None:
        if self.live_session is not None and self.live_session.status().state is CaptureState.RECORDING:
            self.live_session.pause()

    def _stop_live_session(self) -> None:
        session = self.live_session
        if session is None:
            return
        if session.status().state not in {CaptureState.RECORDING, CaptureState.PAUSED, CaptureState.FAILED}:
            return
        # Stopping now drains every queued decode, which can outlast a frame.
        # Run it off the Qt thread so the window stays responsive meanwhile.
        self.btn_live_stop.setEnabled(False)
        self.btn_live_pause.setEnabled(False)
        self.lbl_live_status.setText(
            self._t("Завершение расшифровки…", "Finishing transcription…")
        )
        self._live_stop_thread = threading.Thread(
            target=self._finish_live_session, args=(session,), daemon=True
        )
        self._live_stop_thread.start()

    def _finish_live_session(self, session) -> None:
        try:
            result = session.stop()
        except Exception as exc:
            self.signals.live_event.emit(
                CaptureEvent(CaptureEventKind.STATUS, CaptureSource.MIC, 0, 0, str(exc))
            )
            return
        self.signals.live_finished.emit(result)

    def _on_live_session_update(self, value) -> None:
        if isinstance(value, LiveStatus):
            self.signals.live_status.emit(value)
        else:
            self.signals.live_event.emit(value)

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

    def _update_live_event(self, event) -> None:
        if isinstance(event, TranscriptEvent):
            delta = self._live_transcript_presenter.add_event(event)
            if delta:
                self._append_live_transcript(self._live_transcript_presenter.rendered_delta(event, delta))
        elif isinstance(event, CaptureEvent):
            self._show_live_capture_status(event)
        self._update_live_overlay(event)

    def _show_live_capture_status(self, event: CaptureEvent) -> None:
        key = (event.source, event.detail)
        now = time.monotonic()
        if now - self._live_capture_status_times.get(key, float("-inf")) < 5:
            return
        self._live_capture_status_times[key] = now
        self.lbl_live_status.setText(event.detail)
        # State updates overwrite the status line, so problems get their own
        # banner that survives until the next session starts.
        if event.kind is not CaptureEventKind.DISCONTINUITY:
            self.lbl_live_problem.setText(event.detail)
            self.lbl_live_problem.show()

    def _append_live_transcript(self, text: str) -> None:
        scrollbar = self.live_transcript.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 2
        position = scrollbar.value()
        cursor = self.live_transcript.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(f"{text}\n")
        scrollbar.setValue(scrollbar.maximum() if at_bottom else position)

    def _clear_live_partial(self) -> None:
        self._live_partial_text = ""
        self._live_partial_range = None

    def _clear_live_display(self) -> None:
        self.live_transcript.clear()
        self._live_partial_range = None
        self._live_partial_text = ""
        self._live_transcript_presenter.clear()
        if self.live_session is not None and self.live_session.status().state not in {CaptureState.STOPPED, CaptureState.IDLE}:
            self.live_session.clear_conversation()
        if self.live_overlay is not None:
            self.live_overlay.clear_transcript()
            self.live_overlay.set_conversation(
                self.live_session.conversation() if self.live_session is not None else []
            )

    def _replace_live_partial(self, text: str) -> None:
        previous = self._live_partial_text
        scrollbar = self.live_transcript.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 2
        position = scrollbar.value()
        cursor = self.live_transcript.textCursor()
        if self._live_partial_range is None:
            cursor.movePosition(QTextCursor.MoveOperation.End)
            if cursor.position() > 0:
                cursor.insertText("\n\n")
            start = cursor.position()
        else:
            start, end = self._live_partial_range
            stable = self._stable_text_prefix_length(previous, text)
            cursor.setPosition(start + stable)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            text = text[stable:]
        cursor.insertText(text)
        self._live_partial_text = previous[:stable] + text if previous else text
        self._live_partial_range = (start, start + len(text))
        if previous:
            self._live_partial_range = (start, start + len(self._live_partial_text))
        scrollbar.setValue(scrollbar.maximum() if at_bottom else position)

    @staticmethod
    def _stable_text_prefix_length(previous: str, current: str) -> int:
        shared = 0
        for previous_char, current_char in zip(previous, current, strict=False):
            if previous_char != current_char:
                break
            shared += 1
        if shared == len(previous) or shared == len(current):
            return shared
        return min(previous.rfind(" ", 0, shared), current.rfind(" ", 0, shared)) + 1

    def _render_live_transcript(self) -> None:
        scrollbar = self.live_transcript.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 2
        position = scrollbar.value()
        rendered = self._live_transcript_presenter.rendered_paragraphs()
        self.live_transcript.setPlainText(
            f"{rendered}\n\n{self._live_partial_text}"
            if rendered and self._live_partial_text
            else rendered or self._live_partial_text
        )
        scrollbar.setValue(scrollbar.maximum() if at_bottom else position)

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
        turn = self.live_session.begin_conversation(question)
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
            if status == "chunk":
                self.live_session.append_conversation_answer(self._live_conversation_id, text)
            else:
                self.live_session.finish_conversation(
                    self._live_conversation_id,
                    text,
                    status="error" if status == "error" else "complete",
                )
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

    def _update_live_status(self, status: LiveStatus) -> None:
        labels = {
            CaptureState.IDLE: self._t("Ожидание", "Idle"),
            CaptureState.STARTING: self._t("Запуск", "Starting"),
            CaptureState.RECORDING: self._t("Идёт запись", "Recording"),
            CaptureState.PAUSED: self._t("На паузе", "Paused"),
            CaptureState.STOPPING: self._t("Остановка", "Stopping"),
            CaptureState.STOPPED: self._t("Остановлено", "Stopped"),
            CaptureState.FAILED: self._t("Ошибка", "Failed"),
        }
        self.lbl_live_status.setText(labels[status.state])
        self._update_live_control_state(status.state)

    def _on_live_finished(self, result) -> None:
        self._last_result_dir = str(result.session_dir)
        self.lbl_live_status.setText(self._t("Сессия сохранена", "Session saved"))
        self._update_live_control_state(CaptureState.STOPPED)
        self._sync_live_conversation()

    def _update_live_control_state(self, state: CaptureState | None = None) -> None:
        state = state or (self.live_session.status().state if self.live_session else CaptureState.IDLE)
        self.btn_live_start.setEnabled(state in {CaptureState.IDLE, CaptureState.PAUSED, CaptureState.STOPPED, CaptureState.FAILED})
        self.btn_live_start.setText(
            self._t("ПРОДОЛЖИТЬ", "RESUME")
            if state is CaptureState.PAUSED
            else self._t("НАЧАТЬ ЗАПИСЬ", "START LIVE")
        )
        self.btn_live_pause.setEnabled(state is CaptureState.RECORDING)
        self.btn_live_stop.setEnabled(state in {CaptureState.RECORDING, CaptureState.PAUSED, CaptureState.FAILED})
