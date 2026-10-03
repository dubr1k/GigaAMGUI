"""LLM-обработка GUI, вынесенная из GigaTranscriberQtApp.

Mixin: методы работают со `self` главного окна (виджеты создаются в app_qt).
Поведение сохранено 1:1 — методы перенесены без изменений.
"""
from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFileDialog, QListWidgetItem, QMessageBox

from ..config import LLM_TEMPERATURE
from ..services import cli_tools, llm_service

# Промпты по умолчанию — общие со всеми клиентами (settings_mixin берёт их отсюда).
from ..services.llm_prompts import SUMMARY_PROMPT, TASKS_PROMPT
from .llm_errors import FRIENDLY_LLM_ERRORS


class LlmMixin:
    def _run_llm_provider(
        self,
        llm_settings: dict,
        transcript_text: str,
        prompt: str,
        on_stream_chunk=None,
        cancel_check=None,
    ) -> str:
        raw = llm_settings.get("provider", "API")
        provider = "Other" if self._normalize_llm_provider(raw) == "Other" else raw
        try:
            kwargs = {
                "provider": provider,
                "strict_empty_cli": True,
                "on_stream_chunk": on_stream_chunk,
            }
            if cancel_check is not None:
                kwargs["cancel_check"] = cancel_check
            return llm_service.run_provider(
                llm_settings, transcript_text, prompt,
                **kwargs,
            )
        except llm_service.UnknownLLMProvider as exc:
            raise RuntimeError(self._t(
                f"Неизвестный LLM-провайдер: {exc.provider}",
                f"Unknown LLM provider: {exc.provider}",
            )) from exc

    def _run_llm_processing(self, llm_settings: dict, items: list, modes: list, export_formats: list):
        try:
            results = []
            total = len(items)
            total_operations = total * len(modes)
            completed_operations = 0
            last_name = "llm_result"
            provider = llm_settings.get("provider", "API")
            for item_index, item in enumerate(items, start=1):
                name = item["name"]
                last_name = name
                item_blocks = []
                for mode_suffix, mode_label, prompt in modes:
                    self.log(self._t(
                        f"LLM: обработка {item_index}/{total} — {name} — {mode_label} — {provider}",
                        f"LLM: processing {item_index}/{total} — {name} — {mode_label} — {provider}",
                    ))
                    self.signals.llm_progress_started.emit(completed_operations, total_operations)
                    answer = self._run_llm_provider(
                        llm_settings,
                        item["text"],
                        prompt,
                    )
                    self.signals.llm_response_ready.emit()
                    completed_operations += 1
                    self.signals.llm_progress_update.emit(completed_operations, total_operations)
                    saved_paths = self._save_llm_result(item, answer, mode_suffix, export_formats)
                    block = f"=== {name} / {mode_label} / {provider} ===\n{answer}"
                    if saved_paths:
                        block += self._t("\n\nСохранено:\n", "\n\nSaved:\n") + "\n".join(saved_paths)
                    item_blocks.append(block)
                results.append("\n\n".join(item_blocks))
            mode_suffixes = "_".join(mode[0] for mode in modes)
            self.llm_last_result_name = f"{last_name}_llm_{mode_suffixes}"
            final_text = "\n\n".join(results)
            self.signals.llm_finished.emit(
                True,
                self._t(f"LLM-обработка завершена: {total} файл(ов)", f"LLM processing finished: {total} file(s)"),
                final_text,
            )
        except Exception as e:
            error_text = str(e).strip() or self._t("Неизвестная ошибка", "Unknown error")
            self.signals.llm_finished.emit(
                False,
                self._t("Ошибка LLM: ", "LLM error: ") + self._compact_llm_error(error_text),
                error_text,
            )

    def _compact_llm_error(self, error_text: str, limit: int = 180) -> str:
        raw_text = (error_text or "").strip()
        lowered = raw_text.lower()

        for needles, ru, en in FRIENDLY_LLM_ERRORS:
            if all(needle in lowered for needle in needles):
                return self._t(ru, en)

        text = " ".join(raw_text.split())
        if len(text) <= limit:
            return text
        return text[:limit - 1] + "…"

    def _write_llm_output_file(self, save_path: str, content: str, export_format: str):
        if export_format in ("txt", "md"):
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(content)
            return
        if export_format == "docx":
            from docx import Document
            doc = Document()
            for block in content.split("\n\n"):
                doc.add_paragraph(block)
            doc.save(save_path)

    def _save_llm_result(self, item: dict, answer: str, mode_suffix: str, export_formats: list) -> list[str]:
        source_path = item.get("source_path")
        if self.llm_output_dir:
            target_dir = self.llm_output_dir
        elif source_path:
            target_dir = os.path.dirname(source_path)
        else:
            target_dir = os.getcwd()
        os.makedirs(target_dir, exist_ok=True)
        saved_paths = []
        for export_format in export_formats:
            save_path = os.path.join(target_dir, f"{item['name']}_llm_{mode_suffix}.{export_format}")
            self._write_llm_output_file(save_path, answer, export_format)
            saved_paths.append(save_path)
        return saved_paths

    def _set_llm_buttons_enabled(self, enabled: bool):
        self.btn_llm_process.setEnabled(enabled)
        self.btn_llm_clear.setEnabled(enabled)

    def _update_llm_progress(self, completed: int, total: int):
        if total <= 0:
            return
        percent = round(completed * 100 / total)
        self.progress_bar_llm.setRange(0, 100)
        self.progress_bar_llm.setValue(percent)
        self.lbl_llm_status.setText(
            self._t(
                f"LLM-обработка: {completed} из {total} ({percent}%)",
                f"LLM processing: {completed} of {total} ({percent}%)",
            )
        )

    def _start_llm_progress(self, completed: int, total: int):
        self.progress_bar_llm.setRange(0, 0)
        self.lbl_llm_status.setText(
            self._t(
                f"LLM размышляет и формирует ответ: готово {completed} из {total}…",
                f"LLM is reasoning and generating: {completed} of {total} complete…",
            )
        )

    def _on_llm_response_ready(self):
        self.lbl_llm_status.setText(
            self._t(
                "Ответ LLM получен, сохраняем результат…",
                "LLM response received, saving result…",
            )
        )

    def _on_llm_finished(self, success: bool, message: str, result_text: str):
        self.is_llm_processing = False
        self._set_llm_buttons_enabled(True)
        self.progress_bar_llm.setRange(0, 100)
        if success:
            self.progress_bar_llm.setValue(100)
        self.lbl_llm_status.setText(message)
        if result_text:
            self.llm_last_result_text = result_text
            self.txt_llm_result.setPlainText(result_text)
        elif not success:
            self.llm_last_result_text = ""
            self.txt_llm_result.setPlainText(message)
        if success:
            QMessageBox.information(self, self._t("Готово", "Done"), message)
        else:
            QMessageBox.warning(self, self._t("Ошибка", "Error"), message)

    def _export_llm_result(self, export_format: str):
        result_text = self.txt_llm_result.toPlainText().strip()
        if not result_text:
            QMessageBox.information(self, self._t("Внимание", "Attention"), self._t("Нет результата для экспорта", "There is no result to export."))
            return
        suffix = {"txt": "txt", "md": "md", "docx": "docx"}[export_format]
        initial_dir = self.llm_output_dir or self.output_dir or os.path.expanduser("~")
        default_name = f"{self.llm_last_result_name}.{suffix}"
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            self._t(f"Сохранить результат как {suffix.upper()}", f"Save the result as {suffix.upper()}"),
            os.path.join(initial_dir, default_name),
            f"{suffix.upper()} files (*.{suffix})"
        )
        if not save_path:
            return
        if not save_path.lower().endswith(f".{suffix}"):
            save_path += f".{suffix}"
        try:
            if export_format in ("txt", "md"):
                with open(save_path, "w", encoding="utf-8") as f:
                    f.write(result_text)
            else:
                try:
                    from docx import Document
                except Exception:
                    QMessageBox.warning(self, self._t("Ошибка", "Error"), self._t("Для экспорта в DOCX установите пакет python-docx", "Install the python-docx package to export to DOCX."))
                    return
                doc = Document()
                for block in result_text.split("\n\n"):
                    doc.add_paragraph(block)
                doc.save(save_path)
            target_dir = os.path.dirname(save_path)
            if target_dir:
                self.llm_output_dir = target_dir
                self.user_settings.set_value("llm_output_dir", target_dir)
                self._update_llm_output_dir_label(target_dir)
            self.lbl_llm_status.setText(self._t("Результат экспортирован: ", "Result exported: ") + os.path.basename(save_path))
        except Exception as e:
            QMessageBox.warning(self, self._t("Ошибка", "Error"), self._t("Не удалось экспортировать результат: ", "Failed to export the result: ") + str(e))

    def _is_transcript_already_processed(self, path: str) -> bool:
        """Есть ли уже LLM-результат для этого транскрипта во включённых режиме+формате."""
        target_dir = self.llm_output_dir or os.path.dirname(path)
        if not target_dir or not os.path.isdir(target_dir):
            return False
        modes = [key for key, cb in self.llm_action_checkboxes.items() if cb.isChecked()]
        export_formats = [key for key, cb in self.llm_export_checkboxes.items() if cb.isChecked()]
        if not modes or not export_formats:
            return False
        stem = Path(path).stem
        return any(
            os.path.isfile(os.path.join(target_dir, f"{stem}_llm_{mode}.{fmt}"))
            for mode in modes
            for fmt in export_formats
        )

    def _rebuild_pending_llm_transcripts(self):
        """При старте пересобрать список транскриптов из llm_transcript_dir.

        Показывает только транскрипты без готового LLM-результата — файлы, которые
        уже обработаны выбранными действиями/форматами, пропускаются.
        """
        folder = self.llm_transcript_dir
        # Домашнюю папку прежние версии сохраняли как значение по умолчанию:
        # её сканирование ставило в очередь LLM все .txt/.md пользователя.
        if (
            not folder
            or not os.path.isdir(folder)
            or os.path.realpath(folder) == os.path.realpath(os.path.expanduser("~"))
        ):
            self.transcript_files_for_llm = []
            return
        transcript_exts = (".txt", ".md", ".srt", ".vtt")
        candidates = sorted(
            os.path.join(folder, name)
            for name in os.listdir(folder)
            if name.lower().endswith(transcript_exts) and "_llm_" not in name
        )
        self.transcript_files_for_llm = [
            path for path in candidates if not self._is_transcript_already_processed(path)
        ]

    def _refresh_llm_files_list(self):
        if not hasattr(self, "llm_files_list"):
            return
        self.llm_files_list.clear()
        for path in self.transcript_files_for_llm:
            item = QListWidgetItem(os.path.basename(path))
            item.setToolTip(path)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.llm_files_list.addItem(item)
        has_files = bool(self.transcript_files_for_llm)
        self.llm_files_list.setVisible(has_files)
        self.llm_drop_hint.setVisible(not has_files)
        if has_files:
            self._size_list_to_contents(self.llm_files_list)
        c = self._colors()
        if has_files:
            text = self._t(f"Выбрано транскриптов: {len(self.transcript_files_for_llm)}", f"Selected transcripts: {len(self.transcript_files_for_llm)}")
            self.lbl_llm_files.setText(text)
            self.lbl_llm_files_count.setText(text)
            self.lbl_llm_files.setStyleSheet(self._transparent_label_style(c["text_sub"]))
            self.lbl_llm_files_count.setStyleSheet(self._transparent_label_style(c["text_sub"]))
        else:
            text = self._t("Файлы не выбраны", "No files selected")
            self.lbl_llm_files.setText(text)
            self.lbl_llm_files_count.setText(text)
            self.lbl_llm_files.setStyleSheet(self._transparent_label_style(c["text_mute"]))
            self.lbl_llm_files_count.setStyleSheet(self._transparent_label_style(c["text_mute"]))
        self._update_llm_files_controls()

    def _update_llm_files_controls(self):
        if not hasattr(self, "btn_clear_llm_files"):
            return
        has_files = bool(self.transcript_files_for_llm)
        self.btn_clear_llm_files.setVisible(has_files)
        self.btn_remove_llm_file.setVisible(has_files)
        self.btn_clear_llm_files.setEnabled(has_files and not self.is_llm_processing)
        self.btn_remove_llm_file.setEnabled(bool(self.llm_files_list.selectedItems()) and not self.is_llm_processing)

    def _remove_selected_llm_files(self):
        if self.is_llm_processing:
            return
        selected = {item.data(Qt.ItemDataRole.UserRole) for item in self.llm_files_list.selectedItems()}
        if not selected:
            return
        self.transcript_files_for_llm = [p for p in self.transcript_files_for_llm if p not in selected]
        self._refresh_llm_files_list()

    def _clear_llm_files_list(self):
        if self.is_llm_processing:
            return
        # См. _clear_files_list: папку транскриптов тоже нужно забывать при
        # уже пустом списке, иначе она остаётся запомненной навсегда.
        if not self.transcript_files_for_llm and not self.llm_transcript_dir:
            return
        self.transcript_files_for_llm = []
        self._forget_llm_transcript_dir()
        self._refresh_llm_files_list()

    def _forget_llm_transcript_dir(self):
        """Забыть папку транскриптов, чтобы её не пересканировали на следующем старте."""
        self.llm_transcript_dir = ""
        self.user_settings.set_value("llm_transcript_dir", "")

    def _select_llm_transcript_files(self):
        initial_dir = (
            self.user_settings.get_value("llm_transcript_dir", self.llm_transcript_dir)
            or os.path.expanduser("~")
        )
        files, _ = QFileDialog.getOpenFileNames(
            self,
            self._t("Выберите транскрипты", "Choose transcripts"),
            initial_dir,
            self._t(
                "Транскрипты (*.txt *.md *.srt *.vtt);;Текстовые файлы (*.txt *.md);;Все файлы (*.*)",
                "Transcripts (*.txt *.md *.srt *.vtt);;Text files (*.txt *.md);;All files (*.*)",
            ),
        )
        if files:
            self.transcript_files_for_llm = files
            folder = os.path.dirname(files[0])
            self.llm_transcript_dir = folder
            if not self.llm_output_dir:
                self.llm_output_dir = folder
                self._update_llm_output_dir_label(folder)
            self.user_settings.set_value("llm_transcript_dir", folder)
            self._refresh_llm_files_list()
            self.lbl_llm_status.setText(self._t("Транскрипты готовы к LLM-обработке", "Transcripts are ready for LLM processing"))

    def _select_llm_output_folder(self):
        initial_dir = self.llm_output_dir or self.output_dir or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(
            self, self._t("Выберите папку для сохранения LLM-результатов", "Choose the folder for LLM results"), initial_dir,
        )
        if folder:
            self.llm_output_dir = folder
            self.user_settings.set_value("llm_output_dir", folder)
            self._update_llm_output_dir_label(folder)

    # ──────────────────────────────────────────────────────────────
    # Загрузка по ссылке
    # ──────────────────────────────────────────────────────────────

    def _clear_llm_result(self):
        if self.is_llm_processing:
            QMessageBox.information(self, self._t("Внимание", "Attention"), self._t("LLM-обработка уже выполняется", "LLM processing is already running."))
            return
        self.txt_llm_result.clear()
        self.llm_last_result_text = ""
        self.llm_last_result_name = "llm_result"
        self.lbl_llm_status.setText(self._t("Готово к LLM-обработке", "Ready for LLM processing"))

    def _clear_llm_all(self):
        if self.is_llm_processing:
            QMessageBox.information(self, self._t("Внимание", "Attention"), self._t("LLM-обработка уже выполняется", "LLM processing is already running."))
            return
        self.transcript_files_for_llm = []
        self.txt_llm_transcript.clear()
        self._forget_llm_transcript_dir()
        self._refresh_llm_files_list()
        self._clear_llm_result()
        self.user_settings.set_value("llm_manual_transcript", "")

    def _selected_llm_modes(self):
        modes = []
        if self.llm_action_checkboxes["summary"].isChecked():
            prompt = self.txt_llm_summary_prompt.toPlainText().strip() or SUMMARY_PROMPT
            modes.append(("summary", self._t("Выжимка", "Summary"), prompt))
        if self.llm_action_checkboxes["tasks"].isChecked():
            prompt = self.txt_llm_tasks_prompt.toPlainText().strip() or TASKS_PROMPT
            modes.append(("tasks", self._t("Задачи", "Tasks"), prompt))
        if self.llm_action_checkboxes["custom"].isChecked():
            custom_prompt = self.txt_llm_custom_prompt.toPlainText().strip()
            if not custom_prompt:
                raise ValueError(self._t(
                    "Для режима «Свой промпт» укажите пользовательский промпт в меню «Настройки → LLM API…»",
                    "For “Custom prompt”, enter your prompt in Settings → LLM API…",
                ))
            modes.append(("custom", self._t("Свой промпт", "Custom prompt"), custom_prompt))
        if not modes:
            raise ValueError(self._t(
                "Выберите хотя бы один чекбокс в блоке «Что делать»",
                "Select at least one checkbox under “What to do”",
            ))
        return modes

    def _selected_llm_export_formats(self):
        formats = [key for key, cb in self.llm_export_checkboxes.items() if cb.isChecked()]
        if not formats:
            raise ValueError(self._t(
                "Выберите хотя бы один формат сохранения результата",
                "Select at least one output format for the result",
            ))
        return formats

    def _start_llm_processing(self):
        if self.is_llm_processing:
            return
        try:
            llm_settings = self._collect_llm_settings()
            items = self._collect_llm_inputs()
            modes = self._selected_llm_modes()
            export_formats = self._selected_llm_export_formats()
        except ValueError as e:
            QMessageBox.warning(self, self._t("Внимание", "Attention"), str(e))
            return

        self._save_ui_settings()
        self.is_llm_processing = True
        self._set_llm_buttons_enabled(False)
        self.lbl_llm_status.setText(self._t("Подготовка LLM-запроса…", "Preparing LLM request…"))
        self.progress_bar_llm.setRange(0, 100)
        self.progress_bar_llm.setValue(0)
        self.txt_llm_result.clear()
        threading.Thread(
            target=self._run_llm_processing,
            args=(llm_settings, items, modes, export_formats),
            daemon=True,
        ).start()

    def _collect_llm_settings(self) -> dict:
        provider = self.combo_llm_provider.currentText().strip() or "API"
        api_url = self.entry_llm_api_url.text().strip()
        api_key = self.entry_llm_api_key.text().strip()
        model = self.entry_llm_model.text().strip()
        temperature_text = self.entry_llm_temperature.text().strip() or str(LLM_TEMPERATURE)
        try:
            temperature = float(temperature_text)
        except ValueError as exc:
            raise ValueError(self._t("Temperature должно быть числом", "Temperature must be a number")) from exc
        if not 0 <= temperature <= 2:
            raise ValueError(self._t("Temperature должно быть в диапазоне 0..2", "Temperature must be within 0..2"))

        if provider == "API":
            if not api_url:
                raise ValueError(self._t("Укажите API URL", "Enter the API URL"))
            if not api_key:
                raise ValueError(self._t("Укажите API Key", "Enter the API key"))
            if not model:
                raise ValueError(self._t("Укажите модель", "Enter the model"))
        elif self._normalize_llm_provider(provider) == "Other":
            other_path = self.entry_llm_other_path.text().strip()
            if not other_path:
                raise ValueError(self._t("Укажите команду для провайдера «Другое»", "Specify a command for the 'Other' provider"))
            if not (shutil.which(other_path) or os.path.isfile(other_path)):
                raise ValueError(self._t(f"Не найдена команда: {other_path}", f"Command not found: {other_path}"))
        else:
            spec = cli_tools.provider_by_name(provider)
            requested = getattr(self, f"entry_llm_{spec.settings_prefix}_path").text().strip()
            if cli_tools.locate_tool(spec, requested or None) is None:
                hint = f"\n{self._t('Установка', 'Install')}: {spec.install_hint}" if spec.install_hint else ""
                raise ValueError(
                    self._t(f"Не найден {spec.name}: {requested or spec.binary}", f"{spec.name} not found: {requested or spec.binary}")
                    + hint
                )
        settings = {
            "provider": provider,
            "api_url": api_url,
            "api_key": api_key,
            "model": model,
            "temperature": temperature,
            "other_path": self.entry_llm_other_path.text().strip(),
            "other_args": self.entry_llm_other_args.text().strip(),
            "llm_allow_tools": self.cb_llm_allow_tools.isChecked(),
        }
        for spec in cli_tools.cli_specs():
            prefix = spec.settings_prefix
            settings[f"{prefix}_path"] = getattr(self, f"entry_llm_{prefix}_path").text().strip() or spec.binary
            settings[f"{prefix}_args"] = getattr(self, f"entry_llm_{prefix}_args").text().strip()
            if spec.has_provider_field:
                settings[f"{prefix}_provider"] = getattr(self, f"entry_llm_{prefix}_provider").text().strip()
        return settings

    def _collect_llm_inputs(self):
        manual_text = self.txt_llm_transcript.toPlainText().strip()
        items = []
        if manual_text:
            # Своё имя, даже если рядом выбраны файлы: с именем первого файла
            # результат вставленного текста и результат самого файла писались
            # в один <имя>_llm_<режим>.<формат>, и второй затирал первый.
            # Папка — по-прежнему рядом с первым транскриптом.
            items.append({
                "name": "manual_transcript",
                "text": manual_text,
                "source_path": self.transcript_files_for_llm[0] if self.transcript_files_for_llm else None,
            })
        for path in self.transcript_files_for_llm:
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read().strip()
            except OSError:
                continue
            if text:
                items.append({"name": Path(path).stem, "text": text, "source_path": path})
        if not items:
            raise ValueError(self._t(
                "Выберите хотя бы один транскрипт или вставьте текст вручную",
                "Choose at least one transcript or paste the text",
            ))
        return items

    # ──────────────────────────────────────────────────────────────
    # Обработка файлов
    # ──────────────────────────────────────────────────────────────
