"""What the Live tab shows while a session runs: transcript, status, timer, folder.

The texts that change during a session are listed here once, so the language
switch (_retranslate_live_tab) can translate exactly those and leave a running
status or an error message alone.

Mixin: методы работают со `self` главного окна.
"""

from __future__ import annotations

import time
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextCursor

from ..live.types import CaptureEvent, CaptureEventKind, CaptureState, TranscriptEvent


def _display_path(path: str | Path) -> str:
    """Abbreviate the home folder the way Finder and the Liquid client do."""
    path = Path(path)
    try:
        return str(Path("~") / path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def _session_file_names(paths, session_dir: Path) -> str:
    """Файлы сессии через запятую: имена внутри папки сессии, иначе полный путь."""
    names = []
    for path in paths:
        try:
            names.append(str(Path(path).relative_to(session_dir)))
        except ValueError:
            names.append(str(path))
    return ", ".join(names)


# Подписи, которые меняются по ходу сессии. При смене языка их переводит
# _retranslate_live_tab — только если сейчас показан один из этих текстов.
LIVE_READY_TEXT = ("Готово к записи", "Ready for live capture")
LIVE_STATE_LABELS = {
    CaptureState.IDLE: ("Ожидание", "Idle"),
    CaptureState.STARTING: ("Запуск", "Starting"),
    CaptureState.RECORDING: ("Идёт запись", "Recording"),
    CaptureState.PAUSED: ("На паузе", "Paused"),
    CaptureState.STOPPING: ("Остановка", "Stopping"),
    CaptureState.STOPPED: ("Остановлено", "Stopped"),
    CaptureState.FAILED: ("Ошибка", "Failed"),
}
LIVE_STATUS_TEXTS = (
    LIVE_READY_TEXT,
    *LIVE_STATE_LABELS.values(),
    ("Завершение расшифровки…", "Finishing transcription…"),
    (
        "Загрузка модели распознавания… Запись начнётся, когда она будет готова.",
        "Loading the recognition model… Recording starts once it is ready.",
    ),
    ("Ошибка остановки", "Stop failed"),
    ("Выберите существующую папку сессий", "Select an existing session folder"),
    ("Папка ещё не создана", "The folder does not exist yet"),
)
LIVE_SAVED_PREFIX = ("Сохранено: ", "Saved: ")
# stop() доводит сессию до конца и при сбое этапа, а ошибки отдаёт в
# SessionResult.errors: такая сессия сохранена, но не полностью.
LIVE_SAVED_WITH_ERRORS_PREFIX = ("Сохранено с ошибками: ", "Saved with errors: ")
LIVE_WAVEFORM_TEXTS = {
    "idle": ("Аудиосигнал появится во время записи", "Audio signal appears during capture"),
    "recording": ("Захват аудио", "Capturing audio"),
    "paused": ("Запись на паузе", "Capture paused"),
    "finished": ("Аудиосигнал завершён", "Audio capture complete"),
}


class LiveDisplayMixin:
    def _update_live_recorder_display(self, status) -> None:
        state = getattr(getattr(status, "state", None), "value", "")
        if state == "starting":
            self._live_timer_ticker.stop()
            self._live_timer_elapsed_ms = 0
            self._live_timer_active = False
            self.lbl_live_timer.setText("00:00:00")
        elif state == "recording":
            if not self._live_timer_active:
                self._live_timer_clock.start()
                self._live_timer_active = True
                self._live_timer_ticker.start()
            self.lbl_live_waveform.setText(self._t(*LIVE_WAVEFORM_TEXTS["recording"]))
        elif state == "paused":
            self._pause_live_recorder_timer()
            self.lbl_live_waveform.setText(self._t(*LIVE_WAVEFORM_TEXTS["paused"]))
        elif state in {"stopped", "failed"}:
            self._pause_live_recorder_timer()
            self.lbl_live_waveform.setText(self._t(*LIVE_WAVEFORM_TEXTS["finished"]))

    def _pause_live_recorder_timer(self) -> None:
        if self._live_timer_active:
            self._live_timer_elapsed_ms += self._live_timer_clock.elapsed()
            self._live_timer_active = False
        self._live_timer_ticker.stop()
        self._refresh_live_recorder_timer()

    def _refresh_live_recorder_timer(self) -> None:
        elapsed_ms = self._live_timer_elapsed_ms
        if self._live_timer_active:
            elapsed_ms += self._live_timer_clock.elapsed()
        total_seconds = elapsed_ms // 1000
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.lbl_live_timer.setText(f"{hours:02d}:{minutes:02d}:{seconds:02d}")

    def _update_live_output_folder_label(self, path: str) -> None:
        """Show the folder of the current/last session, or where the next one goes."""
        session_dir = getattr(self, "_live_shown_session_dir", None)
        if session_dir is not None and Path(session_dir).parent != Path(path):
            self._live_shown_session_dir = session_dir = None
        if session_dir is not None:
            text = _display_path(session_dir)
        elif path:
            text = _display_path(path)
        else:
            text = self._t("Папка не выбрана", "Folder not selected")
        label = self.lbl_live_output_folder
        width = label.width()
        # One line, elided in the middle: a wrapped path overflowed the card
        # onto the buttons above it. The tooltip carries the full path.
        shown = (
            label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideMiddle, width)
            if label.isVisible() and width > 40 else text
        )
        label.setText(shown)
        label.setToolTip("\n".join(filter(None, (
            str(session_dir or path),
            self._t(
                "Каждая запись сохраняется в свою папку ГГГГ-ММ-ДД_ЧЧ-ММ-СС: "
                "транскрипт, субтитры и аудио.",
                "Each recording is saved to its own YYYY-MM-DD_HH-MM-SS folder: "
                "transcript, subtitles and audio.",
            ),
        ))))

    def _show_live_session_folder(self, session_dir: Path) -> None:
        self._live_shown_session_dir = Path(session_dir)
        self._update_live_output_folder_label(self.live_output_dir.text().strip())

    def _report_live_result(self, result) -> None:
        """Итог Stop: статус, ошибки этапов остановки и сохранённые файлы в журнале."""
        session_dir = Path(result.session_dir)
        prefix = LIVE_SAVED_WITH_ERRORS_PREFIX if result.errors else LIVE_SAVED_PREFIX
        self.lbl_live_status.setText(self._t(*prefix) + session_dir.name)
        self.lbl_live_status.setToolTip(str(session_dir))
        if result.errors:
            # Баннер, а не только статус: статус перепишет следующая сессия.
            details = "; ".join(result.errors)
            self._report_live_problem(self._t(
                f"Ошибки при остановке: {details}",
                f"Errors while stopping: {details}",
            ))
        self._log_live(self._t("Сессия сохранена: ", "Session saved: ") + str(session_dir))
        if result.exports:
            self._log_live(
                self._t("Расшифровка: ", "Transcript: ") + _session_file_names(result.exports, session_dir)
            )

    def _update_live_event(self, event) -> None:
        if isinstance(event, TranscriptEvent):
            presenter = self._live_transcript_presenter
            delta = presenter.add_event(event)
            if delta:
                piece = presenter.rendered_delta(event, delta)
                if presenter.rewrote:
                    self._redraw_live_transcript(presenter.rendered_pieces())
                else:
                    self._append_live_transcript(piece)
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

    def _redraw_live_transcript(self, pieces: list[str]) -> None:
        scrollbar = self.live_transcript.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 2
        position = scrollbar.value()
        self.live_transcript.setPlainText("".join(f"{piece}\n" for piece in pieces))
        scrollbar.setValue(scrollbar.maximum() if at_bottom else position)

    def _clear_live_display(self) -> None:
        self.live_transcript.clear()
        self._live_transcript_presenter.clear()
        if self.live_session is not None and self.live_session.status().state not in (CaptureState.STOPPED, CaptureState.IDLE):
            try:
                self.live_session.clear_conversation()
            except RuntimeError:
                pass  # разговор уже заморожен остановкой — чистить нечего
        if self.live_overlay is not None:
            self.live_overlay.clear_transcript()
            self.live_overlay.set_conversation(
                self.live_session.conversation() if self.live_session is not None else []
            )

    def _retranslate_live_tab(self, is_ru: bool) -> None:
        """Вкладка Live."""
        if not hasattr(self, "grp_live_source"):
            return
        self.grp_live_source.setTitle("1. Захват в реальном времени" if is_ru else "1. Live capture")
        self.grp_live_output.setTitle("2. Папка сессий" if is_ru else "2. Session folder")
        self.grp_live_exports.setTitle("3. Форматы вывода" if is_ru else "3. Output formats")
        self.lbl_live_source.setText("Источник:" if is_ru else "Source:")
        self.lbl_live_mic_device.setText("Микрофон:" if is_ru else "Microphone:")
        self.lbl_live_system_device.setText("Системный звук:" if is_ru else "System audio:")
        for combo in (self.combo_live_mic_device, self.combo_live_system_device):
            combo.setPlaceholderText("Поиск устройств…" if is_ru else "Looking for devices…")
        self.lbl_live_tracks.setText("Дорожки:" if is_ru else "Tracks:")
        self.lbl_live_diarization.setText("Диаризация:" if is_ru else "Diarization:")
        self.lbl_live_gain.setText("Усиление:" if is_ru else "Gain:")
        self.btn_live_output_select.setText("Выбрать папку" if is_ru else "Choose folder")
        self.btn_live_open_session.setText("Открыть" if is_ru else "Open")
        self.cb_live_mic_audio.setText("Микрофон" if is_ru else "Microphone")
        self.cb_live_system_audio.setText("Системный звук" if is_ru else "System audio")
        self.cb_live_export_txt.setText("Текст" if is_ru else "Text")
        self.cb_live_export_txt_timecodes.setText("Таймкоды" if is_ru else "Timecodes")
        self.cb_live_export_txt_diarize.setText("Диар." if is_ru else "Diar.")
        self.cb_live_export_txt_diarize_timecodes.setText("Диар. + время" if is_ru else "Diar. + time")
        self.cb_live_export_md.setText("Markdown")
        self.cb_live_export_srt.setText("SRT")
        self.cb_live_export_vtt.setText("VTT")
        self.cb_live_subtitle_sentence_split.setText("По предложениям" if is_ru else "By sentences")
        self.lbl_live_subtitle_max_lines.setText("Строк:" if is_ru else "Lines:")
        self.lbl_live_subtitle_max_width.setText("Символов:" if is_ru else "Characters:")
        self.btn_live_pause.setText("Пауза" if is_ru else "Pause")
        self.btn_live_stop.setText("Остановить" if is_ru else "Stop")
        self.btn_live_clear.setText("Очистить" if is_ru else "Clear")
        self.btn_live_overlay.setText("Оверлей" if is_ru else "Overlay")
        source_labels = (("Микрофон", "Microphone"), ("Системный звук", "System audio"), ("Микрофон + системный звук", "Microphone + system audio"))
        for index, labels in enumerate(source_labels):
            self.combo_live_source.setItemText(index, labels[0] if is_ru else labels[1])
        diarization_labels = (("Выключено", "Off"), ("Оценка в реальном времени (недоступно)", "Live estimate (unavailable)"), ("После остановки", "After stop"))
        for index, labels in enumerate(diarization_labels):
            self.combo_live_diarization.setItemText(index, labels[0] if is_ru else labels[1])
        self.combo_live_diarization.setToolTip(
            "Оценки анонимны и могут меняться в последние 10 секунд."
            if is_ru else "Live estimates are anonymous and may change during the most recent 10 seconds."
        )
        self.live_transcript.setPlaceholderText(
            "Расшифровка появится здесь"
            if is_ru else
            "Transcript appears here"
        )
        self._update_live_output_folder_label(self.live_output_dir.text())
        self._update_live_export_controls()
        self._update_live_control_state()
        self._retranslate_known(self.lbl_live_status.setText, self.lbl_live_status.text(), LIVE_STATUS_TEXTS)
        status = self.lbl_live_status.text()
        for pair in (LIVE_SAVED_PREFIX, LIVE_SAVED_WITH_ERRORS_PREFIX):
            prefix = next((prefix for prefix in pair if status.startswith(prefix)), None)
            if prefix is not None:
                self.lbl_live_status.setText(self._t(*pair) + status[len(prefix):])
                break
        self._retranslate_known(
            self.lbl_live_waveform.setText, self.lbl_live_waveform.text(), LIVE_WAVEFORM_TEXTS.values(),
        )
        if self.live_overlay is not None:
            self.live_overlay.set_language(self._lang)
