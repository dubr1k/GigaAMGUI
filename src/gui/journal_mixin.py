"""Вкладка «Журнал»: таблица событий обработки и технический журнал.

Таблица строится из тех же строк, что уходят в технический журнал: каждое
сообщение log() разбирается (_ingest_journal_log) на начало файла, его
длительность, готовность или ошибку.

Mixin: методы работают со `self` главного окна.
"""
from __future__ import annotations

import os
import re
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class JournalMixin:
    def _create_journal_tab(self) -> QWidget:
        """Вкладка «Журнал» (2.0: таблица событий + технический журнал)."""
        log_tab = QWidget()
        log_tab.setObjectName("log_page")
        log_layout = QVBoxLayout(log_tab)
        log_layout.setContentsMargins(self._px(8), self._px(14), self._px(8), self._px(8))
        log_layout.setSpacing(self._px(8))
        journal_heading = QVBoxLayout()
        journal_heading.setSpacing(self._px(2))
        self.journal_title = QLabel("Журнал")
        self.journal_title.setObjectName("page_title")
        journal_heading.addWidget(self.journal_title)
        self.journal_subtitle = QLabel("Текущие события обработки из журнала приложения.")
        self.journal_subtitle.setObjectName("page_subtitle")
        journal_heading.addWidget(self.journal_subtitle)
        log_layout.addLayout(journal_heading)

        journal_controls = QHBoxLayout()
        journal_controls.setSpacing(self._px(6))
        self._journal_filter_buttons = {}
        for key, text in (("all", "Все"), ("ready", "Готово"), ("processing", "В обработке"), ("error", "Ошибка")):
            button = QPushButton(text)
            button.setObjectName("journal_filter")
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setProperty("journal_filter", key)
            button.clicked.connect(self._filter_journal_rows)
            journal_controls.addWidget(button)
            self._journal_filter_buttons[key] = button
        self._journal_filter_buttons["all"].setChecked(True)
        journal_controls.addStretch()
        self.journal_search = QLineEdit()
        self.journal_search.setObjectName("journal_search")
        self.journal_search.setPlaceholderText("Поиск…")
        self.journal_search.setClearButtonEnabled(True)
        self.journal_search.setFixedHeight(self._px(32))
        self.journal_search.setMinimumWidth(self._px(210))
        self.journal_search.textChanged.connect(self._filter_journal_rows)
        journal_controls.addWidget(self.journal_search)
        log_layout.addLayout(journal_controls)

        self.journal_table = QTableWidget(0, 4)
        self.journal_table.setObjectName("journal_table")
        self.journal_table.setHorizontalHeaderLabels(("Файл", "Длительность", "Статус", "Дата"))
        self.journal_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.journal_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.journal_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.journal_table.setShowGrid(False)
        self.journal_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.journal_table.verticalHeader().hide()
        header = self.journal_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.journal_table.setMinimumHeight(self._px(220))
        log_layout.addWidget(self.journal_table, 1)
        self.journal_empty = QLabel("События обработки появятся здесь после запуска.")
        self.journal_empty.setObjectName("journal_empty")
        self.journal_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        log_layout.addWidget(self.journal_empty, 1)

        technical_log = QFrame()
        technical_log.setObjectName("progress_card")
        technical_log_layout = QVBoxLayout(technical_log)
        technical_log_layout.setContentsMargins(self._px(14), self._px(10), self._px(14), self._px(10))
        technical_log_layout.setSpacing(self._px(6))
        log_toolbar = QHBoxLayout()
        self.journal_technical_title = QLabel("Технический журнал")
        self.journal_technical_title.setObjectName("section_title")
        log_toolbar.addWidget(self.journal_technical_title)
        log_toolbar.addStretch()
        self.btn_log_copy = QPushButton("Копировать")
        self.btn_log_copy.setObjectName("text_button")
        self.btn_log_copy.clicked.connect(self._copy_log)
        log_toolbar.addWidget(self.btn_log_copy)
        self.btn_log_save = QPushButton("Сохранить…")
        self.btn_log_save.setObjectName("text_button")
        self.btn_log_save.clicked.connect(self._save_log)
        log_toolbar.addWidget(self.btn_log_save)
        self.btn_log_clear = QPushButton("Очистить журнал")
        self.btn_log_clear.setObjectName("text_button")
        self.btn_log_clear.clicked.connect(self._clear_log)
        log_toolbar.addWidget(self.btn_log_clear)
        technical_log_layout.addLayout(log_toolbar)
        self.log_text = QTextEdit()
        self.log_text.setObjectName("log_document")
        self.log_text.setReadOnly(True)
        self.log_text.setFont(self._font(10, fixed=True))
        self.log_text.setMaximumHeight(self._px(150))
        technical_log_layout.addWidget(self.log_text)
        log_layout.addWidget(technical_log)
        self._refresh_journal_labels()
        return log_tab

    def _copy_log(self):
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.log_text.toPlainText())
            self._set_status(self._t("Журнал скопирован в буфер обмена", "Log copied to clipboard"))

    def _save_log(self):
        if not self.log_text.toPlainText().strip():
            QMessageBox.information(self, self._t("Журнал пуст", "Empty log"), self._t("Журнал пока пуст — нечего сохранять.", "The log is empty — nothing to save."))
            return
        initial_dir = self.user_settings.get_last_output_dir() or self.output_dir or os.path.expanduser("~")
        path, _ = QFileDialog.getSaveFileName(
            self, self._t("Сохранить журнал", "Save log"),
            os.path.join(initial_dir, "transcription_log.txt"),
            self._t("Текстовые файлы (*.txt);;Все файлы (*.*)", "Text files (*.txt);;All files (*.*)")
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.log_text.toPlainText())
            self._set_status(self._t(f"Журнал сохранён: {os.path.basename(path)}", f"Log saved: {os.path.basename(path)}"))
            self.log(f"Журнал сохранён в {path}")
        except OSError as e:
            QMessageBox.warning(self, self._t("Ошибка", "Error"), self._t("Не удалось сохранить журнал:\n", "Failed to save the log:\n") + str(e))

    def _clear_log(self):
        self.log_text.clear()
        self._journal_entries = []
        self._filter_journal_rows()
        self._set_status(self._t("Журнал очищен", "Log cleared"))

    def log(self, message: str):
        message = self._translate_runtime_text(message)
        self.signals.log_message.emit(message)
        self.app_logger.get_logger().info(message)

    def _append_log(self, message: str):
        self.log_text.append(f">> {message}")
        self._ingest_journal_log(message)

    def _refresh_journal_labels(self):
        if not hasattr(self, "journal_table"):
            return
        self.journal_title.setText(self._t("Журнал", "Journal"))
        self.journal_subtitle.setText(self._t(
            "Текущие события обработки из журнала приложения.",
            "Current processing events from the application log.",
        ))
        self.journal_technical_title.setText(self._t("Технический журнал", "Technical log"))
        self.journal_search.setPlaceholderText(self._t("Поиск…", "Search…"))
        self.journal_table.setHorizontalHeaderLabels((
            self._t("Файл", "File"),
            self._t("Длительность", "Duration"),
            self._t("Статус", "Status"),
            self._t("Дата", "Date"),
        ))
        for key, button in self._journal_filter_buttons.items():
            button.setText({
                "all": self._t("Все", "All"),
                "ready": self._t("Готово", "Ready"),
                "processing": self._t("В обработке", "Processing"),
                "error": self._t("Ошибка", "Error"),
            }[key])
        self._filter_journal_rows()

    def _journal_status_label(self, status: str) -> str:
        return {
            "ready": self._t("Готово", "Ready"),
            "processing": self._t("В обработке", "Processing"),
            "error": self._t("Ошибка", "Error"),
        }[status]

    def _journal_active_entry(self, filename: str | None = None) -> dict | None:
        for entry in reversed(getattr(self, "_journal_entries", [])):
            if entry["status"] != "processing":
                continue
            if filename is None or entry["file"] == filename:
                return entry
        return None

    def _journal_record_error(self, filename: str) -> None:
        entry = self._journal_active_entry(filename)
        if entry is None:
            entry = {
                "file": filename, "duration": "—", "status": "error",
                "date": datetime.now().strftime("%d.%m"),
            }
            self._journal_entries.append(entry)
        else:
            entry["status"] = "error"

    def _ingest_journal_log(self, message: str) -> None:
        if not hasattr(self, "journal_table"):
            return
        text = message.strip()
        started = re.match(r"^--- (?:Обработка файла|Processing file) \d+/\d+: (.+) ---$", text)
        duration = re.match(r"^(?:Длительность|Duration): (.+)$", text)
        skipped = re.match(r"^(?:Пропуск файла|Skipping file) (.+)$", text)
        failed = re.match(
            r"^(?:Ошибка при обработке(?: файла)?|Error (?:while )?processing(?: file)?)\s+(.+?)(?::\s|$)",
            text,
        )
        if started:
            self._journal_entries.append({
                "file": started.group(1), "duration": "—", "status": "processing",
                "date": datetime.now().strftime("%d.%m"),
            })
        elif duration:
            entry = self._journal_active_entry()
            if entry is not None:
                entry["duration"] = duration.group(1)
        elif skipped:
            self._journal_record_error(skipped.group(1))
        elif failed:
            self._journal_record_error(failed.group(1))
        elif text.startswith(("ОШИБКА", "ERROR", "Критическая ошибка", "Critical error")):
            entry = self._journal_active_entry()
            if entry is not None:
                entry["status"] = "error"
        elif text.startswith(("Время обработки:", "Processing time:")):
            entry = self._journal_active_entry()
            if entry is not None:
                entry["status"] = "ready"
        self._filter_journal_rows()

    def _filter_journal_rows(self):
        if not hasattr(self, "journal_table"):
            return
        selected_filter = next(
            (key for key, button in self._journal_filter_buttons.items() if button.isChecked()),
            "all",
        )
        query = self.journal_search.text().strip().casefold()
        entries = [
            entry for entry in self._journal_entries
            if (selected_filter == "all" or entry["status"] == selected_filter)
            and (not query or query in entry["file"].casefold() or query in self._journal_status_label(entry["status"]).casefold())
        ]
        self.journal_table.setUpdatesEnabled(False)
        self.journal_table.clearContents()
        self.journal_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            self.journal_table.setItem(row, 0, QTableWidgetItem(entry["file"]))
            self.journal_table.setItem(row, 1, QTableWidgetItem(entry["duration"]))
            self.journal_table.setCellWidget(row, 2, self._journal_status_widget(entry["status"]))
            self.journal_table.setItem(row, 3, QTableWidgetItem(entry["date"]))
            self.journal_table.setRowHeight(row, self._px(32))
        self.journal_table.setUpdatesEnabled(True)
        has_entries = bool(entries)
        self.journal_table.setVisible(has_entries)
        self.journal_empty.setVisible(not has_entries)
        self.journal_empty.setText(
            self._t("События обработки появятся здесь после запуска.", "Processing events will appear here after a run.")
            if not self._journal_entries else self._t("Нет событий по текущему фильтру.", "No events match the current filter.")
        )

    def _journal_status_widget(self, status: str) -> QWidget:
        colors = {"ready": "#22A06B", "processing": "#0A84FF", "error": "#EF4444"}
        container = QWidget()
        container.setObjectName("journal_status_badge")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(self._px(4), 0, self._px(4), 0)
        layout.setSpacing(self._px(6))
        dot = QLabel("●")
        dot.setObjectName("journal_status_dot")
        dot.setStyleSheet(f"color: {colors[status]};")
        layout.addWidget(dot)
        label = QLabel(self._journal_status_label(status))
        label.setObjectName("journal_status_text")
        layout.addWidget(label)
        layout.addStretch()
        return container

    def _retranslate_journal(self, is_ru: bool) -> None:
        """Вкладка «Журнал»."""
        if not hasattr(self, "btn_log_copy"):
            return
        # Заголовки, фильтры, колонки и статусы строк таблицы.
        self._refresh_journal_labels()
        self.btn_log_copy.setText("Копировать" if is_ru else "Copy")
        self.btn_log_copy.setToolTip("Скопировать весь журнал в буфер обмена" if is_ru else "Copy the entire log to the clipboard")
        self.btn_log_save.setText("Сохранить…" if is_ru else "Save…")
        self.btn_log_save.setToolTip("Сохранить журнал в текстовый файл" if is_ru else "Save the log to a text file")
        self.btn_log_clear.setText("Очистить журнал" if is_ru else "Clear log")
        self.btn_log_clear.setToolTip("Очистить только журнал, не сбрасывая настройки" if is_ru else "Clear only the log without resetting settings")
