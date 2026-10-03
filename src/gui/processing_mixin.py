"""Запуск и прогресс обработки файлов для GigaTranscriberQtApp.

Mixin: методы работают со `self` главного окна. Поведение сохранено 1:1.
"""
from __future__ import annotations

import os
import threading
import time

from PyQt6.QtWidgets import (
    QMessageBox,
)

from ..core.model_preparation import (
    PreparationCancelled,
    PreparationError,
    PreparationEvent,
    PreparationState,
)
from ..core.progress import STAGE_LABELS, coerce_progress, stage_label
from ..core.subtitles import SubtitleOptions
from ..services import transcription_service
from ..utils.output_naming import find_output_collisions


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
            # Отменяем именно текущий запуск и забываем его: воркер доделает
            # текущий файл и выйдет, а его итог уже никого не интересует.
            self._abandon_processing_run()
            self.is_processing = False
            self._set_processing_controls_enabled(True)
        self.files_to_process = []
        self.output_dir = ""
        self.files_processed = 0
        self.total_files = 0
        self.time_spent = 0
        self.current_file_start_time = 0
        self.start_time = None
        self.current_stage = None
        self.current_stage_progress = 0.0
        self.start_processing_after_download = False
        self._last_result_dir = ""
        self._last_processing_results = []
        if hasattr(self, "processing_stack"):
            self.processing_stack.setCurrentWidget(self._processing_start_page)
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
        self._forget_input_dir()
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
        if self._processing_cancel_event is not None:
            self._processing_cancel_event.set()
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setText(self._t("Отмена…", "Cancelling…"))
        self.lbl_stage.setText(self._t("●  Останавливаем после текущего файла…", "●  Stopping after the current file…"))
        self.lbl_status.setText(self._t("Отмена: дождитесь завершения текущего файла…", "Cancellation: wait for the current file to finish…"))
        self._set_status(self._t("Отмена обработки…", "Cancelling processing…"))
        self.log(self._t("Запрошена отмена обработки — остановимся после текущего файла", "Cancellation requested — stopping after the current file"))

    def _abandon_processing_run(self) -> None:
        """Отменить текущий запуск и отвязать его от окна.

        Флаг отмены — свой у каждого запуска (Event в snapshot). Общий флаг
        окна следующий «Старт» опускал обратно, и сброшенный воркер
        продолжал свою очередь параллельно новому на той же модели.
        """
        if self._processing_cancel_event is not None:
            self._processing_cancel_event.set()
        self._processing_cancel_event = None
        self._cancel_requested = False

    def _processing_worker_alive(self) -> bool:
        """Жив ли поток последнего запуска — в том числе уже отменённого."""
        is_alive = getattr(self._processing_thread, "is_alive", None)
        return bool(is_alive and is_alive())

    def _start_processing_thread(self):
        if self.is_processing:
            return
        if self._processing_worker_alive():
            # Отмена срабатывает только между файлами: второй воркер на той же
            # модели, пока первый дорабатывает свой файл, конкурирует за неё.
            QMessageBox.information(
                self,
                self._t("Информация", "Information"),
                self._t(
                    "Предыдущая обработка ещё завершает текущий файл. Повторите запуск через несколько секунд.",
                    "The previous run is still finishing its current file. Start again in a few seconds.",
                ),
            )
            return
        if hasattr(self, "processing_stack"):
            self.processing_stack.setCurrentWidget(self._processing_start_page)
        if hasattr(self, "_progress_frame"):
            self._progress_frame.setVisible(True)
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
        collisions = find_output_collisions(self.files_to_process, self.output_dir or None)
        if collisions:
            names = ", ".join(sorted(os.path.basename(path) for group in collisions for path in group))
            QMessageBox.warning(
                self,
                self._t("Коллизия имён", "Output name collision"),
                self._t(
                    f"Файлы с одинаковым базовым именем перезапишут результаты друг друга: {names}. Переименуйте файлы или выберите разные папки.",
                    f"Files with the same base name would overwrite each other's results: {names}. Rename them or use separate folders.",
                ),
            )
            return
        # A failed preparation must not reuse files/results from the previous
        # successful batch when the completion handler decides what to show.
        self._last_generated_transcript_files = []
        self._last_processing_results = []
        self.is_processing = True
        self._cancel_requested = False
        cancel_event = threading.Event()
        self._processing_cancel_event = cancel_event
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
            "audio_preprocessing_mode": self._selected_audio_preprocessing_mode(),
            "selected_formats": self._get_selected_formats(),
            "subtitle_options": SubtitleOptions(
                sentence_split=self.cb_subtitle_sentence_split.isChecked(),
                max_line_count=self.spin_subtitle_max_lines.value(),
                max_line_width=self.spin_subtitle_max_width.value(),
            ),
            "output_dir": self.output_dir,
            "files": list(self.files_to_process),
            "start_time": self.start_time,
            "hf_token": os.getenv("HF_TOKEN", "").strip() or None,
            "cancel_event": cancel_event,
        }
        self._set_processing_controls_enabled(False)
        self._processing_thread = threading.Thread(target=self._process_files, kwargs={"snapshot": snapshot}, daemon=True)
        self._processing_thread.start()

    def _set_processing_controls_enabled(self, enabled: bool):
        self.cb_diarization.setEnabled(enabled)
        self.combo_diarization_backend.setEnabled(enabled)
        self.combo_audio_preprocessing.setEnabled(enabled)
        self._update_diarization_backend_controls()
        self.btn_upload.setEnabled(enabled)
        self.input_path.setEnabled(enabled)
        self._update_files_controls()
        for cb in self.format_checkboxes.values():
            cb.setEnabled(enabled)
        self._update_subtitle_controls_enabled()
        self._sync_diarization_format_controls(controls_enabled=enabled)

    def _process_files(self, snapshot: dict):
        num_speakers = snapshot["num_speakers"]
        enable_diarization = snapshot["enable_diarization"]
        diarization_backend = snapshot["diarization_backend"]
        audio_preprocessing_mode = snapshot["audio_preprocessing_mode"]
        selected_formats = snapshot["selected_formats"]
        subtitle_options = snapshot.get("subtitle_options", SubtitleOptions())
        output_dir = snapshot["output_dir"]
        files = snapshot["files"]
        start_time = snapshot["start_time"]
        hf_token = snapshot.get("hf_token")
        total_files = len(files)
        cancel_event = snapshot.get("cancel_event")
        if cancel_event is None:
            cancel_event = threading.Event()
            self._processing_cancel_event = cancel_event

        def run_active() -> bool:
            # Запуск, сброшенный «Очистить всё», не трогает состояние окна.
            return self._processing_cancel_event is cancel_event

        def report_progress(*args, **kwargs):
            if run_active():
                self._on_file_progress(*args, **kwargs)

        try:
            self._preparation_log_progress = {}
            preparation = transcription_service.build_processing_preparation_plan(
                self.model_loader,
                enable_diarization=enable_diarization,
                diarization_backend=diarization_backend,
                audio_preprocessing_mode=audio_preprocessing_mode,
                hf_token=hf_token,
            )
            prepared = preparation.run(
                self._on_preparation_event,
                cancel_check=cancel_event.is_set,
            )
            processor = transcription_service.build_processor(
                self.model_loader, self.stats, logger=self.log,
                progress_callback=report_progress,
                diarization_manager=prepared.get("diarization"),
                diarization_backend=diarization_backend if enable_diarization else None,
            )
            if enable_diarization:
                self.log(self._t(f"Количество спикеров: {num_speakers if num_speakers else 'автоопределение'}", f"Speaker count: {num_speakers if num_speakers else 'auto-detect'}"))
            files_processed = 0
            files_failed = 0
            failed_names = []
            time_spent = 0.0
            generated_transcript_files = []
            completed_results = []
            for i, filepath in enumerate(files):
                if cancel_event.is_set():
                    self.log(self._t("Обработка отменена пользователем", "Processing cancelled by user"))
                    break
                try:
                    if run_active():
                        self.current_file_start_time = time.time()
                        self.signals.current_file_info.emit(os.path.basename(filepath))
                    file_output_dir = output_dir if output_dir else os.path.dirname(filepath)
                    result = processor.process_file(
                        filepath, file_output_dir, i, total_files,
                        enable_diarization=enable_diarization,
                        diarization_backend=diarization_backend,
                        audio_preprocessing_mode=audio_preprocessing_mode,
                        num_speakers=num_speakers,
                        output_formats=selected_formats,
                        subtitle_options=subtitle_options,
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
                        completed_results.append({
                            "file_path": result.get("file_path", filepath),
                            "file_size": result.get("file_size", 0),
                            "media_duration": result.get("media_duration", 0),
                            "saved_files": list(result.get("saved_files", [])),
                            "diarization": dict(result.get("diarization", {})),
                        })
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
                    if run_active():
                        self.files_processed = files_processed
                        self.time_spent = time_spent
            total_elapsed = time.time() - start_time
            self.log(self._t("=== ОБРАБОТКА ЗАВЕРШЕНА ===", "=== PROCESSING FINISHED ==="))
            self.log(self._t(f"Общее время обработки: {self.time_formatter.format_duration(total_elapsed)}", f"Total processing time: {self.time_formatter.format_duration(total_elapsed)}"))
            self.log(self._t(f"Успешно: {files_processed}/{total_files}" + (f", с ошибками: {files_failed}" if files_failed else ""), f"Successful: {files_processed}/{total_files}" + (f", with errors: {files_failed}" if files_failed else "")))
            cancelled = cancel_event.is_set()
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
            if run_active():
                self._last_generated_transcript_files = generated_transcript_files
                self._last_processing_results = completed_results
            self.signals.processing_finished.emit(success, message, cancel_event)
        except PreparationCancelled:
            self.log(self._t("Подготовка моделей отменена пользователем", "Model preparation cancelled by user"))
            self.signals.processing_finished.emit(False, self._t("Обработка отменена", "Processing cancelled"), cancel_event)
        except PreparationError as e:
            self.signals.processing_finished.emit(
                False,
                self._t(
                    f"Не удалось подготовить компоненты: {e}",
                    f"Failed to prepare components: {e}",
                ),
                cancel_event,
            )
        except Exception as e:
            self.log(self._t(f"Критическая ошибка: {str(e)}", f"Critical error: {str(e)}"))
            self.signals.processing_finished.emit(False, self._t(f"Ошибка: {str(e)}", f"Error: {str(e)}"), cancel_event)
        finally:
            self._release_accelerator_caches()

    def _release_accelerator_caches(self) -> None:
        """Вернуть системе кэши ускорителя, не выгружая сами модели.

        Модели остаются тёплыми для следующей пачки — перезагрузка занимает
        секунды. Освобождаются только буферные пулы, которые иначе держат
        десятки гигабайт unified memory до выхода из приложения.
        """
        loader = getattr(self, "model_loader", None)
        if loader is not None:
            try:
                loader._empty_cache()  # noqa: SLF001
            except Exception:
                pass

        # Пул ASR-бэкенда — не единственный: диаризация считает через torch, и
        # её кэш живёт отдельно от MLX. Без этой чистки после пачки оставалось
        # ~8 ГБ, выделенных драйверу под Sortformer.
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            mps = getattr(torch, "mps", None)
            if mps is not None and torch.backends.mps.is_available():
                mps.empty_cache()
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────
    # Прогресс
    # ──────────────────────────────────────────────────────────────

    _PREPARATION_COMPONENT_NAMES = {
        "asr": ("ASR-модели", "ASR model"),
        "audio-preprocessing": ("аудиопредобработки", "audio preprocessing"),
        "diarization": ("модели диаризации", "diarization model"),
    }

    @staticmethod
    def _format_download_size(value: int | None) -> str:
        if value is None:
            return ""
        amount = float(value)
        for unit in ("B", "KiB", "MiB", "GiB"):
            if amount < 1024.0 or unit == "GiB":
                return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
            amount /= 1024.0
        return ""

    def _on_preparation_event(self, event: PreparationEvent) -> None:
        """Показать проверку, скачивание и загрузку выбранных моделей в журнале."""
        names = self._PREPARATION_COMPONENT_NAMES.get(
            event.component,
            (event.component, event.component),
        )
        component = self._t(*names)
        detail = f" — {event.message}" if event.message else ""

        if event.state is PreparationState.DOWNLOADING and event.completed_bytes is not None:
            if event.total_bytes:
                percent = min(100, int(event.completed_bytes * 100 / event.total_bytes))
                bucket = percent // 10
                key = (event.component, event.state.value)
                if self._preparation_log_progress.get(key) == bucket:
                    return
                self._preparation_log_progress[key] = bucket
                progress = (
                    f" {percent}% "
                    f"({self._format_download_size(event.completed_bytes)} / "
                    f"{self._format_download_size(event.total_bytes)})"
                )
            else:
                progress = f" ({self._format_download_size(event.completed_bytes)})"
        else:
            progress = ""

        if event.state is PreparationState.CHECKING:
            text = self._t(f"Проверка: {component}", f"Checking: {component}")
        elif event.state is PreparationState.DOWNLOADING:
            text = self._t(f"Скачивание {component}", f"Downloading {component}") + progress
        elif event.state is PreparationState.LOADING:
            text = self._t(f"Загрузка {component}", f"Loading {component}")
        elif event.state is PreparationState.READY:
            cached = self._t(" (уже в кэше)", " (already cached)") if event.cached else ""
            text = self._t(f"Готово: {component}", f"Ready: {component}") + cached
        elif event.state is PreparationState.FAILED:
            text = self._t(f"Ошибка подготовки: {component}", f"Preparation failed: {component}")
        else:
            text = self._t(f"Отменено: {component}", f"Cancelled: {component}")
        self.log(text + detail)

    def _on_file_progress(self, event_or_stage, progress: float | None = None):
        # Из потока обработки в Qt-поток уходит уже нормализованный словарь.
        self.signals.stage_update.emit(coerce_progress(event_or_stage, progress).as_dict())

    def _on_stage_update(self, event, progress: float | None = None):
        snapshot = coerce_progress(event, progress)
        stage = snapshot.stage
        stage_progress = snapshot.stage_progress
        if not stage:
            return

        if stage != self.current_stage:
            self.current_stage = stage
            self.current_stage_progress = 0.0 if stage_progress is None else stage_progress
            self._stage_start_time = time.time()

        # Без доли файла и при откате назад полоса файла стоит на месте.
        file_progress = snapshot.file_progress
        if file_progress is None or file_progress < self.current_stage_file_progress:
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

        # Подписи стадий — общие с TUI и веб (STAGE_LABELS); незнакомая стадия — «Подготовка…».
        stage = self.current_stage if self.current_stage in STAGE_LABELS["ru"] else "preparing"
        stage_name = stage_label(stage, "ru" if self._lang == "ru" else "en")
        self.lbl_stage.setText(f"●  {stage_name}{percent_label}")


        if not self.current_stage_is_indeterminate:
            self.progress_bar_file.setFormat("%p%")
        else:
            self.progress_bar_file.setFormat("")

    def _update_current_file_info(self, info: str):
        self.current_stage = None
        self.current_stage_progress = 0.0
        self.current_stage_file_progress = 0.0
        self.current_stage_is_indeterminate = False
        display = info if len(info) <= 64 else f"…{info[-64:]}"
        self._current_filename = display
        self.lbl_current_file.setText(display)

    def _on_processing_finished(self, success: bool, message: str, run=None):
        if run is not None and run is not self._processing_cancel_event:
            # Итог запуска, сброшенного «Очистить всё»: интерфейс уже другой.
            return
        self._processing_cancel_event = None
        self._cancel_requested = False
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
            self.llm_transcript_dir = os.path.dirname(generated_files[0])
            if not self.llm_output_dir:
                self.llm_output_dir = self.llm_transcript_dir
                self._update_llm_output_dir_label(self.llm_output_dir)
            # Список, счётчик и кнопки вкладки LLM — из одного места: раньше
            # здесь менялась только подпись, а список показывал старые файлы.
            self._refresh_llm_files_list()

        if getattr(self, "_last_processing_results", []):
            self._show_processing_result()
        else:
            self._show_completion_dialog(success, message, has_results)
