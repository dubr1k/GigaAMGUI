"""Страница результата обработки на вкладке «Обработка».

Плеер исходного файла, текст с переходом по таймкодам, SRT/диаризация/JSON
и действия с файлами результата; данные — только из реально сохранённых
файлов последнего запуска. Здесь же — диалог завершения, когда показывать
страницу результата нечего.

Mixin: методы работают со `self` главного окна.
"""
from __future__ import annotations

import json
import os
import re

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class ResultViewMixin:
    def _create_processing_result_page(self) -> QWidget:
        result_page = QWidget()
        result_page.setObjectName("processing_result_page")
        result_layout = QVBoxLayout(result_page)
        result_layout.setContentsMargins(self._px(16), self._px(14), self._px(16), self._px(16))
        result_layout.setSpacing(self._px(10))
        result_head = QHBoxLayout()
        result_head.setSpacing(self._px(8))
        result_title_col = QVBoxLayout()
        result_title_col.setSpacing(0)
        self.result_title = QLabel("Результат обработки")
        self.result_title.setObjectName("section_title")
        result_title_col.addWidget(self.result_title)
        self.result_meta = QLabel("")
        self.result_meta.setObjectName("muted_label")
        result_title_col.addWidget(self.result_meta)
        result_head.addLayout(result_title_col, 1)
        self.result_file_picker = QComboBox()
        self.result_file_picker.setObjectName("result_file_picker")
        self.result_file_picker.setMinimumWidth(self._px(170))
        self.result_file_picker.currentIndexChanged.connect(self._select_processing_result)
        result_head.addWidget(self.result_file_picker)
        self.btn_back_to_processing = QPushButton("К обработке")
        self.btn_back_to_processing.setObjectName("secondary_button")
        self.btn_back_to_processing.clicked.connect(lambda: self.processing_stack.setCurrentWidget(self._processing_start_page))
        result_head.addWidget(self.btn_back_to_processing)
        result_layout.addLayout(result_head)
        result_body = QHBoxLayout()
        result_body.setSpacing(self._px(10))
        result_left = QVBoxLayout()
        result_left.setSpacing(self._px(8))
        player_panel = QFrame()
        player_panel.setObjectName("glass_panel")
        player_layout = QVBoxLayout(player_panel)
        player_layout.setContentsMargins(self._px(14), self._px(12), self._px(14), self._px(12))
        player_layout.setSpacing(self._px(7))
        self.result_media_name = QLabel("")
        self.result_media_name.setObjectName("section_title")
        player_layout.addWidget(self.result_media_name)
        self.result_timeline = QSlider(Qt.Orientation.Horizontal)
        self.result_timeline.setObjectName("waveform_timeline")
        self.result_timeline.setRange(0, 0)
        self.result_timeline.sliderReleased.connect(self._seek_result_playback)
        player_layout.addWidget(self.result_timeline)
        time_row = QHBoxLayout()
        self.result_position_label = QLabel("00:00 / 00:00")
        self.result_position_label.setObjectName("muted_label")
        time_row.addWidget(self.result_position_label)
        time_row.addStretch()
        self.result_player_status = QLabel("")
        self.result_player_status.setObjectName("muted_label")
        time_row.addWidget(self.result_player_status)
        player_layout.addLayout(time_row)
        player_controls = QHBoxLayout()
        player_controls.setSpacing(self._px(6))
        self.btn_result_back = QPushButton("−10")
        self.btn_result_back.setObjectName("secondary_button")
        self.btn_result_back.clicked.connect(lambda: self._seek_result_by(-10_000))
        player_controls.addWidget(self.btn_result_back)
        self.btn_result_play = QPushButton("▶")
        self.btn_result_play.setObjectName("primary_button")
        self.btn_result_play.clicked.connect(self._toggle_result_playback)
        player_controls.addWidget(self.btn_result_play)
        self.btn_result_forward = QPushButton("+10")
        self.btn_result_forward.setObjectName("secondary_button")
        self.btn_result_forward.clicked.connect(lambda: self._seek_result_by(10_000))
        player_controls.addWidget(self.btn_result_forward)
        self.result_speed = QComboBox()
        self.result_speed.setObjectName("compact_select")
        for speed in (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0):
            self.result_speed.addItem(f"{speed:g}x", speed)
        self.result_speed.currentIndexChanged.connect(self._set_result_playback_rate)
        player_controls.addWidget(self.result_speed)
        player_controls.addStretch()
        self.result_volume = QSlider(Qt.Orientation.Horizontal)
        self.result_volume.setObjectName("volume_slider")
        self.result_volume.setRange(0, 100)
        self.result_volume.setValue(100)
        self.result_volume.setFixedWidth(self._px(90))
        self.result_volume.valueChanged.connect(self._set_result_volume)
        player_controls.addWidget(self.result_volume)
        player_layout.addLayout(player_controls)
        result_left.addWidget(player_panel)
        self.result_tabs = QTabWidget()
        self.result_tabs.setObjectName("result_tabs")
        self.result_tabs.setUsesScrollButtons(False)
        self.result_transcript = QWidget()
        self.result_transcript_layout = QVBoxLayout(self.result_transcript)
        self.result_transcript_layout.setContentsMargins(self._px(8), self._px(8), self._px(8), self._px(6))
        self.result_transcript_layout.setSpacing(self._px(8))
        self.result_transcript_layout.addStretch()
        self.result_tabs.addTab(self.result_transcript, "Текст")
        self.result_srt = QTextEdit()
        self.result_srt.setObjectName("result_document")
        self.result_srt.setReadOnly(True)
        self.result_tabs.addTab(self.result_srt, "SRT")
        self.result_diarization = QTextEdit()
        self.result_diarization.setObjectName("result_document")
        self.result_diarization.setReadOnly(True)
        self.result_tabs.addTab(self.result_diarization, "Диар.")
        self.result_summary = QTextEdit()
        self.result_summary.setObjectName("result_document")
        self.result_summary.setReadOnly(True)
        self.result_tabs.addTab(self.result_summary, "Итог")
        self.result_json = QTextEdit()
        self.result_json.setObjectName("result_document")
        self.result_json.setReadOnly(True)
        self.result_json.setFont(self._font(9, fixed=True))
        self.result_tabs.addTab(self.result_json, "JSON")
        result_left.addWidget(self.result_tabs, 1)
        result_body.addLayout(result_left, 5)
        result_rail = QFrame()
        result_rail.setObjectName("dense_panel")
        result_rail.setMinimumWidth(self._px(185))
        result_rail.setMaximumWidth(self._px(220))
        rail_layout = QVBoxLayout(result_rail)
        rail_layout.setContentsMargins(self._px(12), self._px(12), self._px(12), self._px(12))
        rail_layout.setSpacing(self._px(7))
        rail_actions_title = QLabel("Действия")
        rail_actions_title.setObjectName("section_title")
        rail_layout.addWidget(rail_actions_title)
        self.result_actions_layout = QVBoxLayout()
        self.result_actions_layout.setSpacing(self._px(2))
        rail_layout.addLayout(self.result_actions_layout)
        rail_topics_title = QLabel("Ключевые темы")
        rail_topics_title.setObjectName("section_title")
        rail_layout.addWidget(rail_topics_title)
        self.result_topics = QLabel("Появятся после LLM-обработки результата.")
        self.result_topics.setObjectName("muted_label")
        self.result_topics.setWordWrap(True)
        rail_layout.addWidget(self.result_topics)
        rail_summary_title = QLabel("Краткое содержание")
        rail_summary_title.setObjectName("section_title")
        rail_layout.addWidget(rail_summary_title)
        self.result_summary_rail = QLabel("Не создавалось автоматически.")
        self.result_summary_rail.setObjectName("muted_label")
        self.result_summary_rail.setWordWrap(True)
        rail_layout.addWidget(self.result_summary_rail)
        rail_layout.addStretch()
        result_body.addWidget(result_rail)
        result_layout.addLayout(result_body, 1)
        return result_page

    def _format_result_time(self, seconds: float) -> str:
        seconds = max(0, int(seconds or 0))
        return f"{seconds // 3600:02d}:{(seconds // 60) % 60:02d}:{seconds % 60:02d}"

    def _format_result_size(self, size: int) -> str:
        amount = float(size or 0)
        for unit in ("Б", "КБ", "МБ", "ГБ"):
            if amount < 1024 or unit == "ГБ":
                return f"{amount:.0f} {unit}" if unit == "Б" else f"{amount:.1f} {unit}"
            amount /= 1024
        return ""

    def _read_result_file(self, path: str | None, fallback: str) -> str:
        if not path or not os.path.isfile(path):
            return fallback
        try:
            with open(path, encoding="utf-8") as source:
                return source.read()
        except OSError as error:
            return self._t(f"Не удалось прочитать файл результата: {error}", f"Could not read result file: {error}")

    def _parse_result_segments(self, path: str | None) -> list[dict]:
        if not path or not os.path.isfile(path):
            return []
        pattern = re.compile(r"^\[(?P<start>[^-]+)\s*-\s*(?P<end>[^]]+)\]\s*(?:(?P<speaker>[^:]+):\s*)?(?P<text>.*)$")
        segments = []
        for line in self._read_result_file(path, "").splitlines():
            match = pattern.match(line.strip())
            if not match:
                continue
            start = match.group("start").strip()
            parts = start.replace(",", ".").split(":")
            try:
                seconds = sum(float(value) * 60 ** index for index, value in enumerate(reversed(parts)))
            except ValueError:
                seconds = 0.0
            segments.append({"start": start, "seconds": seconds, "speaker": (match.group("speaker") or "").strip(), "text": match.group("text").strip()})
        return segments

    def _show_processing_result(self):
        records = getattr(self, "_last_processing_results", [])
        if not records or not hasattr(self, "result_file_picker"):
            return
        blocked = self.result_file_picker.blockSignals(True)
        self.result_file_picker.clear()
        for record in records:
            self.result_file_picker.addItem(os.path.basename(record["file_path"]), record)
        self.result_file_picker.blockSignals(blocked)
        self.result_file_picker.setCurrentIndex(0)
        self._populate_processing_result(records[0])
        self.processing_stack.setCurrentWidget(self._processing_result_page)
        self._show_tab("processing")

    def _select_processing_result(self, index: int):
        record = self.result_file_picker.itemData(index)
        if isinstance(record, dict):
            self._populate_processing_result(record)

    def _ensure_result_player(self) -> bool:
        if hasattr(self, "_result_player"):
            return self._result_player is not None
        try:
            from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
        except ImportError:
            self._result_player = None
            self.result_player_status.setText(self._t("Воспроизведение недоступно в этой сборке.", "Playback is unavailable in this build."))
            return False
        self._result_audio_output = QAudioOutput(self)
        self._result_player = QMediaPlayer(self)
        self._result_player.setAudioOutput(self._result_audio_output)
        self._result_player.positionChanged.connect(self._sync_result_player_position)
        self._result_player.durationChanged.connect(self._sync_result_player_duration)
        self._result_player.playbackStateChanged.connect(self._sync_result_playback_state)
        return True

    def _populate_processing_result(self, record: dict):
        source_path = record["file_path"]
        saved_files = [path for path in record.get("saved_files", []) if os.path.isfile(path)]
        duration = float(record.get("media_duration") or 0)
        self._active_processing_result = record
        self.result_title.setText(os.path.basename(source_path))
        media_info = " · ".join(part for part in (self._format_result_time(duration) if duration else "", self._format_result_size(record.get("file_size", 0)), os.path.splitext(source_path)[1].removeprefix(".").upper()) if part)
        self.result_meta.setText(media_info)
        self.result_media_name.setText(os.path.basename(source_path))
        self.result_timeline.setRange(0, max(0, int(duration * 1000)))
        self.result_timeline.setValue(0)
        self.result_position_label.setText(f"00:00 / {self._format_result_time(duration)}")
        has_player = self._ensure_result_player()
        for control in (self.btn_result_back, self.btn_result_play, self.btn_result_forward, self.result_speed, self.result_volume, self.result_timeline):
            control.setEnabled(has_player and os.path.isfile(source_path))
        if has_player and os.path.isfile(source_path):
            self._result_player.stop()
            self._result_player.setSource(QUrl.fromLocalFile(source_path))
            self._result_audio_output.setVolume(self.result_volume.value() / 100)
            self.result_player_status.setText("")

        plain_text = next((path for path in saved_files if path.lower().endswith(".txt") and "_timecodes" not in path and "_diarize" not in path), None)
        timed_text = next((path for path in saved_files if path.lower().endswith("_timecodes.txt")), None)
        diarized_text = next((path for path in saved_files if "_diarize" in os.path.basename(path).lower() and path.lower().endswith(".txt")), None)
        srt = next((path for path in saved_files if path.lower().endswith(".srt")), None)
        segments = self._parse_result_segments(timed_text)
        text = self._read_result_file(plain_text, self._t("Текстовый экспорт для этого запуска не создавался.", "A plain-text export was not created for this run."))
        self._result_text = text
        self._populate_result_transcript(segments, text)
        self.result_srt.setPlainText(self._read_result_file(srt, self._t("SRT не создавался для этого запуска.", "SRT was not created for this run.")))
        self.result_diarization.setPlainText(self._read_result_file(diarized_text, self._t("Диаризация не применялась или не создала отдельный экспорт.", "Diarization was not applied or did not create a separate export.")))
        self.result_summary.setPlainText(self._t("Краткое содержание не создавалось автоматически. Отправьте готовый текст на вкладку LLM, чтобы создать его.", "No summary was generated automatically. Send the completed text to the LLM tab to create one."))
        self.result_json.setPlainText(json.dumps(record, ensure_ascii=False, indent=2))
        self._populate_result_actions(saved_files)
        self.result_topics.setText(self._t("Ключевые темы появятся после LLM-обработки результата.", "Key topics appear after LLM processing of this result."))
        self.result_summary_rail.setText(self._t("Не создавалось автоматически.", "Not generated automatically."))

    @classmethod
    def _clear_layout(cls, layout) -> None:
        """Убрать из layout всё, включая вложенные layout'ы и их виджеты.

        takeAt() возвращает вложенный layout без виджетов: сами виджеты
        остаются детьми страницы, поэтому каждая строка сегмента после
        повторного заполнения оставалась на экране вторым экземпляром.
        """
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout() is not None:
                cls._clear_layout(item.layout())
                item.layout().deleteLater()

    def _populate_result_transcript(self, segments: list[dict], fallback_text: str):
        layout = self.result_transcript_layout
        self._clear_layout(layout)
        if not segments:
            label = QLabel(fallback_text)
            label.setWordWrap(True)
            label.setObjectName("result_segment")
            layout.addWidget(label)
        for segment in segments:
            row = QVBoxLayout()
            meta = QHBoxLayout()
            time_button = QPushButton(segment["start"])
            time_button.setObjectName("text_button")
            time_button.clicked.connect(lambda _checked=False, position=int(segment["seconds"] * 1000): self._seek_result_to(position))
            meta.addWidget(time_button)
            if segment["speaker"]:
                badge = QLabel(segment["speaker"])
                badge.setObjectName("speaker_badge")
                meta.addWidget(badge)
            meta.addStretch()
            row.addLayout(meta)
            body = QLabel(segment["text"])
            body.setWordWrap(True)
            body.setObjectName("result_segment")
            row.addWidget(body)
            layout.addLayout(row)
        layout.addStretch()

    def _populate_result_actions(self, saved_files: list[str]):
        layout = self.result_actions_layout
        self._clear_layout(layout)
        show_folder = QPushButton(self._t("Показать результаты", "Show results"))
        show_folder.setObjectName("text_button")
        show_folder.clicked.connect(self._open_results_folder)
        layout.addWidget(show_folder)
        copy_text = QPushButton(self._t("Копировать текст", "Copy text"))
        copy_text.setObjectName("text_button")
        copy_text.clicked.connect(self._copy_result_text)
        layout.addWidget(copy_text)
        for path in saved_files:
            suffix = os.path.splitext(path)[1].removeprefix(".").upper()
            action = QPushButton(self._t(f"Открыть {suffix}", f"Open {suffix}"))
            action.setObjectName("text_button")
            action.clicked.connect(lambda _checked=False, result_path=path: QDesktopServices.openUrl(QUrl.fromLocalFile(result_path)))
            layout.addWidget(action)

    def _copy_result_text(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(getattr(self, "_result_text", ""))
        self._set_status(self._t("Текст скопирован", "Text copied"))

    def _sync_result_player_position(self, position: int):
        if self.result_timeline.isSliderDown():
            return
        self.result_timeline.setValue(position)
        total = max(0, self.result_timeline.maximum())
        self.result_position_label.setText(f"{self._format_result_time(position / 1000)} / {self._format_result_time(total / 1000)}")

    def _sync_result_player_duration(self, duration: int):
        if duration > 0:
            self.result_timeline.setMaximum(duration)

    def _sync_result_playback_state(self, state):
        playing = str(state).endswith("PlayingState")
        self.btn_result_play.setText("❚❚" if playing else "▶")

    def _toggle_result_playback(self):
        if not self._ensure_result_player():
            return
        if self._result_player.playbackState().name == "PlayingState":
            self._result_player.pause()
        else:
            self._result_player.play()

    def _seek_result_playback(self):
        self._seek_result_to(self.result_timeline.value())

    def _seek_result_to(self, position: int):
        self.result_timeline.setValue(position)
        if self._ensure_result_player():
            self._result_player.setPosition(position)

    def _seek_result_by(self, delta: int):
        self._seek_result_to(max(0, min(self.result_timeline.maximum(), self.result_timeline.value() + delta)))

    def _set_result_playback_rate(self, _index: int):
        if self._ensure_result_player():
            self._result_player.setPlaybackRate(float(self.result_speed.currentData() or 1.0))

    def _set_result_volume(self, value: int):
        if self._ensure_result_player():
            self._result_audio_output.setVolume(value / 100)

    def _show_completion_dialog(self, success: bool, message: str, has_results: bool):
        """Кастомный диалог завершения.

        Заменяет нативный QMessageBox: на macOS у него кнопки получались разной
        высоты (кнопка по умолчанию рисуется нативно), а иконка-«!» выглядела
        тревожно. Здесь обе кнопки — обычные QPushButton в одном ряду с общей
        высотой, поэтому они всегда на одном уровне.
        """
        c = self._colors()
        dlg = QDialog(self)
        dlg.setModal(True)
        dlg.setWindowTitle(self._t("Готово", "Done") if success else self._t("Завершено", "Finished"))

        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(self._px(24), self._px(22), self._px(24), self._px(18))
        outer.setSpacing(self._px(16))

        head = QHBoxLayout()
        head.setSpacing(self._px(14))
        glyph = QLabel("✓" if success else "⚠")
        glyph.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        glyph.setStyleSheet(
            f"color: {c['accent'] if success else c['clear_hover_text']};"
            f" font-size: {self._pt_css(24)}pt; font-weight: bold; background: transparent;"
        )
        head.addWidget(glyph)

        text_col = QVBoxLayout()
        text_col.setSpacing(self._px(4))
        title = QLabel(
            self._t("Обработка завершена!", "Processing completed!") if success
            else self._t("Обработка остановлена", "Processing stopped")
        )
        title.setStyleSheet(
            f"color: {c['text']}; font-size: {self._pt_css(13)}pt; font-weight: bold; background: transparent;"
        )
        text_col.addWidget(title)
        body = QLabel(message)
        body.setWordWrap(True)
        body.setStyleSheet(
            f"color: {c['text_sub']}; font-size: {self._pt_css(10)}pt; background: transparent;"
        )
        text_col.addWidget(body)
        head.addLayout(text_col, 1)
        outer.addLayout(head)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(self._px(10))
        btn_row.addStretch()
        btn_h = self._px(34)
        clicked = {"open": False}
        if has_results:
            open_btn = QPushButton(self._t("Открыть папку с результатами", "Open results folder"))
            open_btn.setObjectName("open_result_button")
            open_btn.setFixedHeight(btn_h)
            open_btn.clicked.connect(lambda: (clicked.__setitem__("open", True), dlg.accept()))
            btn_row.addWidget(open_btn)
        ok_btn = QPushButton(self._t("ОК", "OK"))
        ok_btn.setObjectName("start_button")
        ok_btn.setFixedHeight(btn_h)
        ok_btn.setMinimumWidth(self._px(96))
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(ok_btn)
        outer.addLayout(btn_row)

        dlg.exec()
        if clicked["open"]:
            self._open_results_folder()
