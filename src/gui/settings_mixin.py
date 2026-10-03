"""Сохранение/восстановление UI-настроек, геометрии окна, папки данных и результатов.

Mixin: методы работают со `self` главного окна. Поведение сохранено 1:1.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import QByteArray, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from ..config import (
    AUDIO_PREPROCESSING_MODE,
    LLM_API_KEY,
    LLM_API_URL,
    LLM_MODEL,
    LLM_TEMPERATURE,
    save_env_value,
)
from ..data_paths import save_data_dir_selection
from ..services import cli_tools
from .llm_mixin import SUMMARY_PROMPT, TASKS_PROMPT


class SettingsMixin:
    def _select_data_directory(self):
        """Сохранить новый единый data root; он применится после restart."""
        current = os.environ.get("GIGAAM_DATA_DIR") or os.path.expanduser("~")
        selected = QFileDialog.getExistingDirectory(
            self,
            self._t("Папка данных GigaAM", "GigaAM data directory"),
            current,
        )
        if not selected:
            return
        try:
            save_data_dir_selection(selected)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(
                self,
                self._t("Папка данных", "Data directory"),
                self._t("Не удалось сохранить выбор:\n", "Could not save the selection:\n") + str(exc),
            )
            return
        if hasattr(self, "settings_data_dir_value"):
            self.settings_data_dir_value.setText(selected)
        QMessageBox.information(
            self,
            self._t("Папка данных", "Data directory"),
            self._t(
                f"Новая папка сохранена:\n{selected}\n\nПерезапустите приложение. "
                "Существующие модели автоматически не перемещаются.",
                f"The new directory has been saved:\n{selected}\n\nRestart the application. "
                "Existing models are not moved automatically.",
            ),
        )

    def _restore_geometry(self):
        # Геометрию из безголовых/тестовых сессий не восстанавливаем
        if self._is_headless():
            return
        saved = self.user_settings.settings.get("window_geometry")
        if saved:
            try:
                self.restoreGeometry(QByteArray.fromHex(saved.encode("ascii")))
            except Exception:
                pass

    def _save_geometry(self):
        if self._is_headless():
            return
        try:
            self.user_settings.settings["window_geometry"] = bytes(
                self.saveGeometry().toHex()
            ).decode("ascii")
            self.user_settings._save_settings()
        except Exception:
            pass

    def _set_status(self, message: str):
        """Дублирует ключевые сообщения в системный статус-бар."""
        if hasattr(self, "status_bar") and self.status_bar is not None:
            self.status_bar.showMessage(self._translate_runtime_text(message))

    def _open_results_folder(self):
        target = self._last_result_dir or self.output_dir or self.input_dir
        if not target or not os.path.isdir(target):
            QMessageBox.information(
                self,
                self._t("Папка недоступна", "Folder unavailable"),
                self._t("Папка с результатами ещё не определена.\nЗапустите обработку или выберите папку сохранения.", "The results folder has not been determined yet.\nStart processing or choose an output folder."),
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    def _restore_ui_settings(self):
        saved_formats = self.user_settings.get_value("output_formats", {}) or {}
        for fmt, cb in self.format_checkboxes.items():
            cb.blockSignals(True)
            cb.setChecked(bool(saved_formats.get(fmt, self.output_formats.get(fmt, False))))
            cb.blockSignals(False)
            self.output_formats[fmt] = cb.isChecked()

        self.cb_subtitle_sentence_split.setChecked(bool(
            self.user_settings.get_value("subtitle_sentence_split", True)
        ))
        self.spin_subtitle_max_lines.setValue(int(
            self.user_settings.get_value("subtitle_max_line_count", 2) or 2
        ))
        self.spin_subtitle_max_width.setValue(int(
            self.user_settings.get_value("subtitle_max_line_width", 64) or 64
        ))
        self._update_subtitle_controls_enabled()

        diarization_enabled = bool(self.user_settings.get_value("enable_diarization", False))
        diarization_backend = self.user_settings.get_value("diarization_backend", "pyannote")
        num_speakers = int(self.user_settings.get_value("num_speakers", 0) or 0)
        backend_index = self.combo_diarization_backend.findData(diarization_backend)
        self.combo_diarization_backend.blockSignals(True)
        self.combo_diarization_backend.setCurrentIndex(backend_index if backend_index >= 0 else 0)
        self.combo_diarization_backend.blockSignals(False)
        self.diarization_backend = self.combo_diarization_backend.currentData() or "pyannote"
        self.cb_diarization.blockSignals(True)
        self.cb_diarization.setChecked(diarization_enabled)
        self.cb_diarization.blockSignals(False)
        self.enable_diarization = diarization_enabled
        self.entry_num_speakers.setValue(num_speakers)
        self._update_diarization_backend_controls()
        self._sync_diarization_format_controls(controls_enabled=not self.is_processing)

        preprocessing_mode = self.user_settings.get_value(
            "audio_preprocessing_mode", AUDIO_PREPROCESSING_MODE
        )
        preprocessing_index = self.combo_audio_preprocessing.findData(preprocessing_mode)
        self.combo_audio_preprocessing.setCurrentIndex(
            preprocessing_index if preprocessing_index >= 0 else 0
        )

        provider = self._normalize_llm_provider(self.user_settings.get_value("llm_provider", "API"))
        display_provider = ("Другое" if self._lang == "ru" else "Other") if provider == "Other" else provider
        index = self.combo_llm_provider.findText(display_provider)
        self.combo_llm_provider.setCurrentIndex(index if index >= 0 else 0)
        self.entry_llm_api_url.setText(self.user_settings.get_value("llm_api_url", LLM_API_URL))
        self.entry_llm_api_key.setText(LLM_API_KEY)
        self.entry_llm_model.setText(self.user_settings.get_value("llm_model", LLM_MODEL))
        self.entry_llm_temperature.setText(str(self.user_settings.get_value("llm_temperature", LLM_TEMPERATURE)))
        # Пути к CLI не замораживаются: пустое поле = автопоиск при каждом запуске (cli_tools),
        # в настройках остаётся только то, что пользователь ввёл сам. Голое имя бинаря
        # («claude») — наследие старого дефолта, равносильно пустому.
        for spec in cli_tools.cli_specs():
            saved_path = self.user_settings.get_value(f"llm_{spec.settings_prefix}_path", "") or ""
            getattr(self, f"entry_llm_{spec.settings_prefix}_path").setText(
                "" if saved_path.strip() == spec.binary else saved_path
            )
            getattr(self, f"entry_llm_{spec.settings_prefix}_args").setText(
                self.user_settings.get_value(f"llm_{spec.settings_prefix}_args", "")
            )
            if spec.has_provider_field:
                getattr(self, f"entry_llm_{spec.settings_prefix}_provider").setText(
                    self.user_settings.get_value(f"llm_{spec.settings_prefix}_provider", "")
                )
        self.entry_llm_other_path.setText(self.user_settings.get_value("llm_other_path", ""))
        self.entry_llm_other_args.setText(self.user_settings.get_value("llm_other_args", ""))
        self.cb_llm_allow_tools.setChecked(bool(self.user_settings.get_value("llm_allow_tools", False)))
        self._update_llm_provider_fields(self.combo_llm_provider.currentText())
        self._refresh_llm_tools()
        self.txt_llm_summary_prompt.setPlainText(self.user_settings.get_value("llm_summary_prompt", SUMMARY_PROMPT))
        self.txt_llm_tasks_prompt.setPlainText(self.user_settings.get_value("llm_tasks_prompt", TASKS_PROMPT))
        self.txt_llm_custom_prompt.setPlainText(self.user_settings.get_value("llm_custom_prompt", ""))
        self.txt_llm_transcript.setPlainText(self.user_settings.get_value("llm_manual_transcript", ""))

        tab_index = int(self.user_settings.get_value("active_tab_index", 0) or 0)
        if 0 <= tab_index < self.tabs.count():
            self.tabs.setCurrentIndex(tab_index)

        self._rebuild_pending_audio_files()
        if self.files_to_process:
            self._refresh_files_list()

        saved_llm_actions = self.user_settings.get_value("llm_actions", {}) or {}
        for key, cb in self.llm_action_checkboxes.items():
            cb.setChecked(bool(saved_llm_actions.get(key, cb.isChecked())))

        saved_llm_exports = self.user_settings.get_value("llm_export_formats", {}) or {}
        for key, cb in self.llm_export_checkboxes.items():
            cb.setChecked(bool(saved_llm_exports.get(key, cb.isChecked())))

        saved_asr_model = self.user_settings.get_value("asr_model", "")
        if isinstance(saved_asr_model, str) and saved_asr_model:
            try:
                self.model_loader.configure_model(saved_asr_model)
            except ValueError:
                pass

        saved_asr_backend = self.user_settings.get_value("asr_backend", "")
        if isinstance(saved_asr_backend, str) and saved_asr_backend:
            try:
                self.model_loader.configure_backend(saved_asr_backend)
            except Exception:
                self.model_loader.configure_backend("auto")

        saved_onnx_provider = self.user_settings.get_value("onnx_provider", "")
        if isinstance(saved_onnx_provider, str) and saved_onnx_provider:
            try:
                self.model_loader.configure_onnx_runtime(provider=saved_onnx_provider)
            except ValueError:
                self.model_loader.configure_onnx_runtime(provider="auto")

        self._rebuild_pending_llm_transcripts()
        self._refresh_llm_files_list()
        if hasattr(self, "_restore_support_surface_settings"):
            self._restore_support_surface_settings()

    def _save_ui_settings(self):
        self.user_settings.set_value("output_formats", self.output_formats)
        self.user_settings.set_value(
            "subtitle_sentence_split", self.cb_subtitle_sentence_split.isChecked()
        )
        self.user_settings.set_value(
            "subtitle_max_line_count", self.spin_subtitle_max_lines.value()
        )
        self.user_settings.set_value(
            "subtitle_max_line_width", self.spin_subtitle_max_width.value()
        )
        self.user_settings.set_value("enable_diarization", self.cb_diarization.isChecked())
        self.user_settings.set_value("diarization_backend", self.combo_diarization_backend.currentData())
        self.user_settings.set_value("num_speakers", self.entry_num_speakers.value())
        self.user_settings.set_value(
            "audio_preprocessing_mode", self._selected_audio_preprocessing_mode()
        )
        self.user_settings.set_value("asr_backend", self.model_loader.requested_backend)
        self.user_settings.set_value("onnx_provider", self.model_loader.requested_provider)
        self.user_settings.set_value("asr_model", self.model_loader.requested_model)
        self.user_settings.set_value("llm_provider", self._normalize_llm_provider(self.combo_llm_provider.currentText()))
        self.user_settings.set_value("llm_api_url", self.entry_llm_api_url.text().strip())
        llm_api_key = self.entry_llm_api_key.text().strip()
        # Пустое поле при сохранённом ключе — это удаление ключа: раньше .env
        # писался только для непустого значения, и стереть ключ было нельзя.
        if llm_api_key != os.environ.get("LLM_API_KEY", "").strip():
            try:
                save_env_value("LLM_API_KEY", llm_api_key)
            except OSError as exc:
                self.log(self._t(f"Не удалось сохранить LLM API key: {exc}", f"Could not save the LLM API key: {exc}"))
        self.user_settings.set_value("llm_model", self.entry_llm_model.text().strip())
        self.user_settings.set_value("llm_temperature", self.entry_llm_temperature.text().strip())
        for spec in cli_tools.cli_specs():
            self.user_settings.set_value(
                f"llm_{spec.settings_prefix}_path", getattr(self, f"entry_llm_{spec.settings_prefix}_path").text().strip()
            )
            self.user_settings.set_value(
                f"llm_{spec.settings_prefix}_args", getattr(self, f"entry_llm_{spec.settings_prefix}_args").text().strip()
            )
            if spec.has_provider_field:
                self.user_settings.set_value(
                    f"llm_{spec.settings_prefix}_provider",
                    getattr(self, f"entry_llm_{spec.settings_prefix}_provider").text().strip(),
                )
        self.user_settings.set_value("llm_allow_tools", self.cb_llm_allow_tools.isChecked())
        self.user_settings.set_value("llm_other_path", self.entry_llm_other_path.text().strip())
        self.user_settings.set_value("llm_other_args", self.entry_llm_other_args.text().strip())
        self.user_settings.set_value("llm_summary_prompt", self.txt_llm_summary_prompt.toPlainText())
        self.user_settings.set_value("llm_tasks_prompt", self.txt_llm_tasks_prompt.toPlainText())
        self.user_settings.set_value("llm_custom_prompt", self.txt_llm_custom_prompt.toPlainText())
        self.user_settings.set_value("llm_manual_transcript", self.txt_llm_transcript.toPlainText())
        self.user_settings.set_value("active_tab_index", self.tabs.currentIndex())
        self.user_settings.set_value("llm_actions", {k: cb.isChecked() for k, cb in self.llm_action_checkboxes.items()})
        self.user_settings.set_value("llm_export_formats", {k: cb.isChecked() for k, cb in self.llm_export_checkboxes.items()})
        if self.llm_output_dir:
            self.user_settings.set_value("llm_output_dir", self.llm_output_dir)
        if self.llm_transcript_dir:
            self.user_settings.set_value("llm_transcript_dir", self.llm_transcript_dir)
        if hasattr(self, "live_session"):
            self._save_live_settings()
