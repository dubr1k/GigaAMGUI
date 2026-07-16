"""Запуск и прогресс обработки файлов для GigaTranscriberQtApp.

Mixin: методы работают со `self` главного окна. Поведение сохранено 1:1.
"""
from __future__ import annotations

import os
import threading
import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..core.progress import ProgressEvent
from ..services import transcription_service


class ProcessingMixin:
    def _clear_all(self):
        if self.is_processing:
            reply = QMessageBox.question(
                self,
                self._t("Внимание", "Attention"),
                self._t("Идет обработка файлов. Вы уверены, что хотите сбросить все настройки?", "File processing is in progress. Are you sure you want to reset all settings?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.No:
                return
            self._cancel_requested = True
            self.is_processing = False
            self._set_processing_controls_enabled(True)
        self.files_to_process = []
        self.output_dir = ""
        self.input_dir = ""
        self.files_processed = 0
        self.total_files = 0
        self.time_spent = 0
        self.current_file_start_time = 0
        self.start_time = None
        self.current_stage = None
        self.current_stage_progress = 0.0
        self.start_processing_after_download = False
        self._last_result_dir = ""
        self.log_text.clear()
        self.progress_bar_total.setValue(0)
        self.progress_bar_file.setValue(0)
        self.progress_upload.setValue(0)
        self.progress_upload.setVisible(False)
        self.lbl_current_file.setText("")
        self.lbl_stage.setText("")
        self.lbl_file_counter.setText("")
        self.detail_row.setVisible(False)
        self.lbl_status.setText(self._t("Готов к работе", "Ready to work"))
        self.btn_cancel.setVisible(False)
        self.btn_cancel.setEnabled(True)
        self.btn_cancel.setText(self._t("Отменить", "Cancel"))
        self.btn_open_result.setVisible(False)
        c = self._colors()
        self._refresh_files_list()
        self.lbl_input_folder.setText(self._t("Папка не выбрана", "Folder not selected"))
        self.lbl_input_folder.setStyleSheet(self._transparent_label_style(c["text_mute"], font_pt=9))
        self.lbl_output_folder.setText(self._t("Папка не выбрана (по умолчанию - рядом с файлом)", "Folder not selected (default: next to the file)"))
        self.lbl_output_folder.setStyleSheet(self._transparent_label_style(c["text_mute"]))
        self.input_path.clear()
        self.btn_start.setEnabled(True)
        self.btn_upload.setEnabled(True)
        self._set_status(self._t("Готов к работе", "Ready to work"))
        self.log(self._t("Все настройки сброшены", "All settings have been reset"))

    def _cancel_processing(self):
        if not self.is_processing or self._cancel_requested:
            return
        self._cancel_requested = True
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setText(self._t("Отмена…", "Cancelling…"))
        self.lbl_stage.setText(self._t("●  Останавливаем после текущего файла…", "●  Stopping after the current file…"))
        self.lbl_status.setText(self._t("Отмена: дождитесь завершения текущего файла…", "Cancellation: wait for the current file to finish…"))
        self._set_status(self._t("Отмена обработки…", "Cancelling processing…"))
        self.log(self._t("Запрошена отмена обработки — остановимся после текущего файла", "Cancellation requested — stopping after the current file"))

    def _start_processing_thread(self):
        if self.is_processing:
            return
        if self.is_downloading:
            QMessageBox.information(self, self._t("Информация", "Information"), self._t("Дождитесь завершения загрузки по ссылке.", "Wait for the URL download to finish."))
            return
        if self.input_path.text().strip():
            self._start_download(start_after_download=True)
            return
        if not self.files_to_process:
            QMessageBox.warning(self, self._t("Внимание", "Attention"), self._t("Выберите хотя бы один файл для обработки!", "Choose at least one file for processing!"))
            return
        if not any(self.output_formats.values()):
            QMessageBox.warning(self, self._t("Внимание", "Attention"), self._t("Выберите хотя бы один формат вывода!", "Choose at least one output format!"))
            return
        if not self.output_dir:
            self.log(self._t("Папка сохранения не выбрана. Результаты будут сохраняться рядом с каждым исходным файлом.", "Output folder not selected. Results will be saved next to each source file."))
        self.is_processing = True
        self._cancel_requested = False
        self.start_time = time.time()
        self.files_processed = 0
        self.total_files = len(self.files_to_process)
        self.time_spent = 0
        self.current_file_start_time = 0
        self.current_stage = None
        self.current_stage_progress = 0.0
        self.log(self._t("Подготовка к обработке...", "Preparing for processing..."))
        self.btn_start.setEnabled(False)
        self.btn_start.setText(self._t("ИДЕТ ОБРАБОТКА...", "PROCESSING..."))
        self.progress_bar_total.setValue(0)
        self.progress_bar_file.setValue(0)
        self._stage_start_time = 0.0
        self.detail_row.setVisible(True)
        self.btn_cancel.setEnabled(True)
        self.btn_cancel.setText(self._t("Отменить", "Cancel"))
        self.btn_cancel.setVisible(True)
        self.btn_open_result.setVisible(False)
        self._last_result_dir = self.output_dir or os.path.dirname(self.files_to_process[0])
        self.lbl_file_counter.setText(self._t(f"Файл 1 / {self.total_files}", f"File 1 / {self.total_files}"))
        self.lbl_current_file.setText("")
        self.lbl_stage.setText(self._t("●  Подготовка…", "●  Preparing…"))
        self.lbl_status.setText(self._t(f"Обработка {self.total_files} файлов…", f"Processing {self.total_files} files…"))
        self._set_status(self._t(f"Обработка {self.total_files} файлов…", f"Processing {self.total_files} files…"))
        num_speakers = None
        if self.enable_diarization and self.diarization_backend != "sortformer":
            value = self.entry_num_speakers.value()
            if value > 0:
                num_speakers = value
        snapshot = {
            "num_speakers": num_speakers,
            "enable_diarization": self.enable_diarization,
            "diarization_backend": self.diarization_backend,
            "selected_formats": self._get_selected_formats(),
            "output_dir": self.output_dir,
            "files": list(self.files_to_process),
            "start_time": self.start_time,
        }
        self._set_processing_controls_enabled(False)
        threading.Thread(target=self._process_files, kwargs={"snapshot": snapshot}, daemon=True).start()

    def _set_processing_controls_enabled(self, enabled: bool):
        self.cb_diarization.setEnabled(enabled)
        self.combo_diarization_backend.setEnabled(enabled)
        self._update_diarization_backend_controls()
        self.btn_upload.setEnabled(enabled)
        self.input_path.setEnabled(enabled)
        self._update_files_controls()
        for cb in self.format_checkboxes.values():
            cb.setEnabled(enabled)
        if enabled:
            for fmt in ('txt_diarize', 'txt_diarize_timecodes'):
                cb = self.format_checkboxes.get(fmt)
                if cb:
                    cb.setEnabled(self.enable_diarization)

    def _process_files(self, snapshot: dict):
        num_speakers = snapshot["num_speakers"]
        enable_diarization = snapshot["enable_diarization"]
        diarization_backend = snapshot["diarization_backend"]
        selected_formats = snapshot["selected_formats"]
        output_dir = snapshot["output_dir"]
        files = snapshot["files"]
        start_time = snapshot["start_time"]
        total_files = len(files)
        try:
            if not self.model_loader.load_model(self.log):
                self.signals.processing_finished.emit(False, self._t("Не удалось загрузить модель", "Failed to load model"))
                return
            processor = transcription_service.build_processor(
                self.model_loader, self.stats, logger=self.log,
                progress_callback=self._on_file_progress,
            )
            if enable_diarization:
                self.log(self._t(f"Количество спикеров: {num_speakers if num_speakers else 'автоопределение'}", f"Speaker count: {num_speakers if num_speakers else 'auto-detect'}"))
            files_processed = 0
            files_failed = 0
            failed_names = []
            time_spent = 0.0
            generated_transcript_files = []
            for i, filepath in enumerate(files):
                if self._cancel_requested:
                    self.log(self._t("Обработка отменена пользователем", "Processing cancelled by user"))
                    break
                try:
                    self.current_file_start_time = time.time()
                    self.signals.current_file_info.emit(os.path.basename(filepath))
                    file_output_dir = output_dir if output_dir else os.path.dirname(filepath)
                    result = processor.process_file(
                        filepath, file_output_dir, i, total_files,
                        enable_diarization=enable_diarization,
                        diarization_backend=diarization_backend,
                        num_speakers=num_speakers,
                        output_formats=selected_formats
                    )
                    self.stats.add_processing_record(
                        file_path=result['file_path'],
                        file_size=result['file_size'],
                        duration=result.get('media_duration', 0),
                        conversion_time=result['conversion_time'],
                        transcription_time=result['transcription_time'],
                        success=result['success']
                    )
                    if result['success']:
                        files_processed += 1
                        for saved_file in result.get('saved_files', []):
                            if saved_file.lower().endswith(('.txt', '.md', '.srt', '.vtt')):
                                generated_transcript_files.append(saved_file)
                    else:
                        files_failed += 1
                        failed_names.append(os.path.basename(filepath))
                    time_spent += result['total_time']
                except Exception as e:
                    files_failed += 1
                    failed_names.append(os.path.basename(filepath))
                    self.log(self._t(f"Ошибка при обработке файла {os.path.basename(filepath)}: {str(e)}", f"Error while processing file {os.path.basename(filepath)}: {str(e)}"))
                    continue
                finally:
                    self.files_processed = files_processed
                    self.time_spent = time_spent
            total_elapsed = time.time() - start_time
            self.log(self._t("=== ОБРАБОТКА ЗАВЕРШЕНА ===", "=== PROCESSING FINISHED ==="))
            self.log(self._t(f"Общее время обработки: {self.time_formatter.format_duration(total_elapsed)}", f"Total processing time: {self.time_formatter.format_duration(total_elapsed)}"))
            self.log(self._t(f"Успешно: {files_processed}/{total_files}" + (f", с ошибками: {files_failed}" if files_failed else ""), f"Successful: {files_processed}/{total_files}" + (f", with errors: {files_failed}" if files_failed else "")))
            cancelled = self._cancel_requested
            duration_str = self.time_formatter.format_duration(total_elapsed)
            if cancelled:
                message = self._t(f"Отменено. Обработано {files_processed}/{total_files} за {duration_str}", f"Cancelled. Processed {files_processed}/{total_files} in {duration_str}")
            elif files_failed:
                message = self._t(f"Готово с ошибками: {files_processed}/{total_files} успешно за {duration_str}", f"Completed with errors: {files_processed}/{total_files} successful in {duration_str}")
            else:
                message = self._t(f"Завершено за {duration_str}", f"Completed in {duration_str}")
            if failed_names:
                shown = ", ".join(failed_names[:5])
                if len(failed_names) > 5:
                    shown += self._t(f" и ещё {len(failed_names) - 5}", f" and {len(failed_names) - 5} more")
                message += self._t(f"\nНе удалось: {shown}\nПодробности — на вкладке «Журнал обработки».", f"\nFailed: {shown}\nSee details in the 'Processing log' tab.")
            success = (files_processed > 0) and (files_failed == 0) and not cancelled
            self._last_generated_transcript_files = generated_transcript_files
            self.signals.processing_finished.emit(success, message)
        except Exception as e:
            self.log(self._t(f"Критическая ошибка: {str(e)}", f"Critical error: {str(e)}"))
            self.signals.processing_finished.emit(False, self._t(f"Ошибка: {str(e)}", f"Error: {str(e)}"))

    # ──────────────────────────────────────────────────────────────
    # Прогресс
    # ──────────────────────────────────────────────────────────────

    _STAGE_NAMES = {
        'preparing': ('Подготовка…', 'Preparing…'),
        'conversion': ('Конвертация…', 'Converting…'),
        'transcription': ('Распознавание речи…', 'Speech recognition…'),
        'diarization': ('Диаризация…', 'Speaker diarization…'),
        'export': ('Экспорт…', 'Exporting…'),
        'finalizing': ('Завершение…', 'Finalizing…'),
    }

    def _on_file_progress(self, event_or_stage, progress: float | None = None):
        if isinstance(event_or_stage, ProgressEvent):
            event = {
                "stage": event_or_stage.stage,
                "file_progress": event_or_stage.file_progress,
                "stage_progress": event_or_stage.stage_progress,
            }
        elif isinstance(event_or_stage, dict):
            event = event_or_stage
        else:
            event = {
                "stage": event_or_stage,
                "file_progress": float(progress or 0.0),
                "stage_progress": None,
            }

        self.signals.stage_update.emit(event)

    def _on_stage_update(self, event, progress: float | None = None):
        if isinstance(event, ProgressEvent):
            stage = event.stage
            file_progress = event.file_progress
            stage_progress = event.stage_progress
        elif isinstance(event, dict):
            stage = event.get("stage")
            file_progress = float(event.get("file_progress", 0.0) or 0.0)
            stage_progress = event.get("stage_progress")
        else:
            stage = event
            file_progress = float(progress or 0.0)
            stage_progress = None

        if not stage:
            return

        if stage != self.current_stage:
            self.current_stage = str(stage)
            self.current_stage_progress = 0.0 if stage_progress is None else stage_progress
            self._stage_start_time = time.time()

        if file_progress < self.current_stage_file_progress:
            file_progress = self.current_stage_file_progress
        self.current_stage_file_progress = file_progress
        self.current_stage_is_indeterminate = stage_progress is None
        if stage_progress is not None:
            self.current_stage_progress = stage_progress

        self._refresh_progress()

    def _refresh_progress(self):
        if not self.is_processing or self.total_files == 0 or not self.files_to_process:
            return

        files_done = min(self.files_processed, self.total_files)
        file_progress = self.current_stage_file_progress
        file_progress = max(0.0, min(file_progress, 1.0))

        overall = (files_done + file_progress) / self.total_files
        self.progress_bar_total.setValue(int(overall * 100))

        if self.current_stage_is_indeterminate:
            self.progress_bar_file.setRange(0, 0)
            self.progress_bar_file.setValue(0)
            if self.current_stage_progress is None:
                percent_label = "…"
            else:
                percent_label = ""
        else:
            self.progress_bar_file.setRange(0, 100)
            self.progress_bar_file.setValue(int(file_progress * 100))
            percent_label = f"  {int(file_progress * 100)}%"

        current_idx = min(files_done + 1, self.total_files)
        self.lbl_file_counter.setText(self._t(f"Файл {current_idx} / {self.total_files}", f"File {current_idx} / {self.total_files}"))

        stage_pair = self._STAGE_NAMES.get(self.current_stage or '', ('Подготовка…', 'Preparing…'))
        stage_name = stage_pair[0] if self._lang == 'ru' else stage_pair[1]
        self.lbl_stage.setText(f"●  {stage_name}{percent_label}")


        if not self.current_stage_is_indeterminate:
            self.progress_bar_file.setFormat("%p%")
        else:
            self.progress_bar_file.setFormat("")

    def _update_total_progress(self, value: int):
        self.progress_bar_total.setValue(value)

    def _update_file_progress(self, value: int):
        self.progress_bar_file.setValue(value)

    def _update_current_file_info(self, info: str):
        self.current_stage = None
        self.current_stage_progress = 0.0
        self.current_stage_file_progress = 0.0
        self.current_stage_is_indeterminate = False
        display = info if len(info) <= 64 else f"…{info[-64:]}"
        self._current_filename = display
        self.lbl_current_file.setText(display)

    def _on_processing_finished(self, success: bool, message: str):
        self.is_processing = False
        self.btn_start.setEnabled(True)
        self.btn_start.setText(self._t("ЗАПУСТИТЬ ОБРАБОТКУ", "START PROCESSING"))
        self.btn_cancel.setVisible(False)
        self.btn_cancel.setEnabled(True)
        self.btn_cancel.setText(self._t("Отменить", "Cancel"))
        self.progress_bar_total.setValue(100 if success else self.progress_bar_total.value())
        self.progress_bar_file.setRange(0, 100)
        self.progress_bar_file.setValue(100 if success else self.progress_bar_file.value())
        self.lbl_stage.setText(self._t("✓  Готово", "✓  Done") if success else self._t("✕  Остановлено", "✕  Stopped"))
        self.lbl_status.setText(message.split("\n")[0])
        self._set_status(message.split("\n")[0])
        self._set_processing_controls_enabled(True)

        has_results = bool(self._last_result_dir) and os.path.isdir(self._last_result_dir)
        self.btn_open_result.setVisible(has_results)

        generated_files = [p for p in self._last_generated_transcript_files if os.path.isfile(p)]
        if generated_files:
            self.transcript_files_for_llm = generated_files
            self.user_settings.set_value("last_selected_transcript_files", generated_files)
            self.llm_transcript_dir = os.path.dirname(generated_files[0])
            if not self.llm_output_dir:
                self.llm_output_dir = self.llm_transcript_dir
                self._update_llm_output_dir_label(self.llm_output_dir)
            self.lbl_llm_files.setText(self._t(f"Выбрано транскриптов: {len(generated_files)}", f"Selected transcripts: {len(generated_files)}"))
            self.lbl_llm_files.setStyleSheet(self._transparent_label_style(self._colors()["text_sub"]))

        self._show_completion_dialog(success, message, has_results)

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

    def closeEvent(self, event):
        if self.is_processing or self.is_downloading:
            reply = QMessageBox.question(
                self,
                self._t("Внимание", "Attention"),
                self._t("Идёт обработка/загрузка. Закрыть приложение и прервать её?", "Processing/download is in progress. Close the app and interrupt it?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return
            self._cancel_requested = True
            self.is_processing = False
        if self.output_dir:
            self.user_settings.set_last_output_dir(self.output_dir)
        if self.input_dir:
            self.user_settings.set_last_files_dir(self.input_dir)
        self._save_ui_settings()
        self._save_geometry()
        self.app_logger.log_session_end()
        event.accept()

    # ──────────────────────────────────────────────────────────────
    # Ошибки загрузки модели
    # ──────────────────────────────────────────────────────────────

    def _show_model_error(self, message: str):
        QMessageBox.warning(self, self._t("Ошибка загрузки", "Model loading error"), message)
