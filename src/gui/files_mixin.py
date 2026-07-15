"""Список файлов, drag-and-drop и выбор файлов/папок для GigaTranscriberQtApp.

Mixin: методы работают со `self` главного окна. Поведение сохранено 1:1.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
)

from ..config import MEDIA_EXTENSIONS, save_env_value


class FilesMixin:
    def _refresh_files_list(self):
        """Синхронизирует QListWidget и пустое состояние с self.files_to_process."""
        if not hasattr(self, "files_list"):
            return
        self.files_list.clear()
        for path in self.files_to_process:
            item = QListWidgetItem(os.path.basename(path))
            item.setToolTip(path)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.files_list.addItem(item)
        has_files = bool(self.files_to_process)
        self.files_list.setVisible(has_files)
        self.drop_hint.setVisible(not has_files)
        if has_files:
            self._size_list_to_contents(self.files_list)
        c = self._colors()
        if has_files:
            self.lbl_files_count.setText(self._t(f"Выбрано файлов: {len(self.files_to_process)}", f"Selected files: {len(self.files_to_process)}"))
            self.lbl_files_count.setStyleSheet(self._transparent_label_style(c["text_sub"]))
        else:
            self.lbl_files_count.setText("Файлы не выбраны")
            self.lbl_files_count.setStyleSheet(self._transparent_label_style(c["text_mute"]))
        self._update_files_controls()

    def _update_files_controls(self):
        if not hasattr(self, "btn_clear_files"):
            return
        has_files = bool(self.files_to_process)
        self.btn_clear_files.setEnabled(has_files and not self.is_processing)
        self.btn_remove_file.setEnabled(
            bool(self.files_list.selectedItems()) and not self.is_processing
        )

    def _remove_selected_files(self):
        if self.is_processing:
            return
        selected = {
            item.data(Qt.ItemDataRole.UserRole) for item in self.files_list.selectedItems()
        }
        if not selected:
            return
        removed = len(selected)
        self.files_to_process = [p for p in self.files_to_process if p not in selected]
        self._refresh_files_list()
        self.log(f"Убрано из очереди: {removed} файлов")

    def _clear_files_list(self):
        if self.is_processing or not self.files_to_process:
            return
        self.files_to_process = []
        self._refresh_files_list()
        self.log("Очередь файлов очищена")

    def keyPressEvent(self, event):
        if (
            event.key() == Qt.Key.Key_Delete
            and hasattr(self, "files_list")
            and self.files_list.hasFocus()
        ):
            self._remove_selected_files()
            return
        if (
            event.key() == Qt.Key.Key_Delete
            and hasattr(self, "llm_files_list")
            and self.llm_files_list.hasFocus()
        ):
            self._remove_selected_llm_files()
            return
        super().keyPressEvent(event)

    def _toggle_diarization(self, state):
        if self._diarization_prompt_open:
            return
        enabling = (state == Qt.CheckState.Checked.value)
        backend = self.combo_diarization_backend.currentData() or "pyannote"
        if enabling and backend == "pyannote" and not os.getenv("HF_TOKEN", "").startswith("hf_"):
            self._diarization_prompt_open = True
            self.cb_diarization.setEnabled(False)
            try:
                if not self._show_hf_token_dialog():
                    self.cb_diarization.blockSignals(True)
                    self.cb_diarization.setChecked(False)
                    self.cb_diarization.blockSignals(False)
                    self.enable_diarization = False
                    self.entry_num_speakers.setEnabled(False)
                    for fmt in ('txt_diarize', 'txt_diarize_timecodes'):
                        cb = self.format_checkboxes.get(fmt)
                        if cb:
                            cb.setEnabled(False)
                    return
            finally:
                self.cb_diarization.setEnabled(True)
                self._diarization_prompt_open = False
        self.enable_diarization = enabling
        self.diarization_backend = backend
        self._update_diarization_backend_controls()
        for fmt in ('txt_diarize', 'txt_diarize_timecodes'):
            cb = self.format_checkboxes.get(fmt)
            if cb:
                cb.setEnabled(self.enable_diarization)
        if self.enable_diarization:
            self.log("Диаризация спикеров: ВКЛЮЧЕНА")
        else:
            self.entry_num_speakers.setValue(0)
            self.log("Диаризация спикеров: ВЫКЛЮЧЕНА")

    def _change_diarization_backend(self, _index=None):
        self.diarization_backend = self.combo_diarization_backend.currentData() or "pyannote"
        if (
            self.enable_diarization
            and self.diarization_backend == "pyannote"
            and not os.getenv("HF_TOKEN", "").startswith("hf_")
        ):
            self.cb_diarization.setChecked(False)
        self._update_diarization_backend_controls()

    def _update_diarization_backend_controls(self):
        is_sortformer = self.diarization_backend == "sortformer"
        self.btn_hf_token.setEnabled(not is_sortformer and not self.is_processing)
        self.entry_num_speakers.setEnabled(
            self.enable_diarization and not is_sortformer and not self.is_processing
        )
        self.lbl_diarization_info.setText(
            self._t(
                "NVIDIA Sortformer: автоопределение, максимум 4 спикера; нужен NeMo",
                "NVIDIA Sortformer: auto-detect, up to 4 speakers; NeMo required",
            )
            if is_sortformer
            else self._t(
                "Pyannote: автоопределение спикеров (требуется HF_TOKEN)",
                "Pyannote: automatic speaker detection (HF_TOKEN required)",
            )
        )

    def _toggle_format(self, fmt: str):
        self.output_formats[fmt] = self.format_checkboxes[fmt].isChecked()

    def _get_selected_formats(self) -> list:
        return [fmt for fmt, enabled in self.output_formats.items() if enabled]

    # ──────────────────────────────────────────────────────────────
    # Файлы / папки
    # ──────────────────────────────────────────────────────────────

    def open_paths_from_system(self, paths: list, append: bool = True):
        """Open files received from Finder, Dock, CLI args, or another app instance."""
        media_files, transcript_files = self._collect_supported_open_paths(paths)
        if media_files:
            if hasattr(self, "tabs"):
                self.tabs.setCurrentIndex(0)
            self._apply_dropped_or_selected_files(media_files, append=append)
        if transcript_files:
            if hasattr(self, "tabs"):
                self.tabs.setCurrentIndex(1)
            self.transcript_files_for_llm = transcript_files if not append else self._merge_paths(
                self.transcript_files_for_llm, transcript_files
            )
            folder = os.path.dirname(transcript_files[0])
            self.llm_transcript_dir = folder
            self.user_settings.set_value("llm_transcript_dir", folder)
            self.user_settings.set_value("last_selected_transcript_files", self.transcript_files_for_llm)
            self._refresh_llm_files_list()
            self.lbl_llm_status.setText(self._t("Транскрипты готовы к LLM-обработке", "Transcripts are ready for LLM processing"))
        if not media_files and not transcript_files and paths:
            QMessageBox.information(
                self,
                self._t("Неподдерживаемый формат", "Unsupported format"),
                self._t("Файлы не являются поддерживаемыми медиа или транскриптами (.txt, .md, .srt, .vtt).", "Files are not supported media or transcript files (.txt, .md, .srt, .vtt)."),
            )
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _merge_paths(self, existing_paths: list, new_paths: list) -> list:
        merged = []
        seen = set()
        for path in list(existing_paths) + list(new_paths):
            normalized = os.path.abspath(path)
            if normalized not in seen:
                merged.append(path)
                seen.add(normalized)
        return merged

    def _collect_supported_open_paths(self, paths: list):
        transcript_exts = (".txt", ".md", ".srt", ".vtt")
        media_files = []
        transcript_files = []
        for raw_path in paths:
            path = os.path.abspath(os.path.expanduser(str(raw_path)))
            if os.path.isdir(path):
                for root, _dirs, filenames in os.walk(path):
                    for filename in filenames:
                        full = os.path.join(root, filename)
                        lower = filename.lower()
                        if lower.endswith(MEDIA_EXTENSIONS):
                            media_files.append(full)
                        elif lower.endswith(transcript_exts):
                            transcript_files.append(full)
            elif os.path.isfile(path):
                lower = path.lower()
                if lower.endswith(MEDIA_EXTENSIONS):
                    media_files.append(path)
                elif lower.endswith(transcript_exts):
                    transcript_files.append(path)
        return self._merge_paths([], media_files), self._merge_paths([], transcript_files)

    def _select_files(self):
        initial_dir = self.user_settings.get_last_files_dir() or self.input_dir or os.path.expanduser("~")
        files, _ = QFileDialog.getOpenFileNames(
            self, "Выберите аудио или видео файлы", initial_dir,
            "Медиа файлы (*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.mp4 *.avi *.mov *.mkv *.webm *.wma *.qta *.3gp);;Все файлы (*.*)"
        )
        if files:
            self._apply_dropped_or_selected_files(files)

    def _apply_dropped_or_selected_files(self, files: list, append: bool = False, remember_dir: bool = True):
        if not files:
            return
        unique_files = []
        seen = set()
        for file_path in files:
            normalized = os.path.abspath(file_path)
            if normalized not in seen:
                unique_files.append(file_path)
                seen.add(normalized)
        if append:
            existing = {os.path.abspath(f) for f in self.files_to_process}
            for file_path in unique_files:
                normalized = os.path.abspath(file_path)
                if normalized not in existing:
                    self.files_to_process.append(file_path)
                    existing.add(normalized)
        else:
            self.files_to_process = unique_files
        if remember_dir:
            file_dir = os.path.dirname(unique_files[0])
            self.input_dir = file_dir
            self.user_settings.set_last_files_dir(file_dir)
        self._refresh_files_list()
        self.user_settings.set_value("last_selected_audio_files", [p for p in self.files_to_process if os.path.isfile(p)])
        self.log(f"Добавлено в очередь: {len(unique_files)} файлов")
        for f in unique_files:
            self.log(f" + {os.path.basename(f)}")

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            self._set_drop_active(True)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._set_drop_active(False)
        super().dragLeaveEvent(event)

    def _set_drop_active(self, active: bool):
        self._drop_active = active
        if hasattr(self, "drop_hint"):
            self._style_drop_hint()

    def dropEvent(self, event: QDropEvent):
        self._set_drop_active(False)
        urls = event.mimeData().urls()
        if not urls:
            event.acceptProposedAction()
            return

        self.open_paths_from_system([url.toLocalFile() for url in urls if url.toLocalFile()], append=True)
        event.acceptProposedAction()

    def _select_files_folder(self):
        initial_dir = self.user_settings.get_last_files_dir() or self.input_dir or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку с аудио/видео файлами", initial_dir)
        if folder:
            self.input_dir = folder
            self.user_settings.set_last_files_dir(folder)
            self._update_input_dir_label(folder)
            files = []
            for root, _dirs, filenames in os.walk(folder):
                for f in filenames:
                    if f.lower().endswith(MEDIA_EXTENSIONS):
                        files.append(os.path.join(root, f))
            if files:
                self.files_to_process = files
                self._refresh_files_list()
                self.user_settings.set_value("last_selected_audio_files", [p for p in self.files_to_process if os.path.isfile(p)])
                self.log(f"Добавлено из папки (включая подпапки): {len(files)} файлов")
                for f in files:
                    self.log(f" + {os.path.relpath(f, folder)}")
            else:
                QMessageBox.information(self, self._t("Информация", "Information"), self._t("В выбранной папке и подпапках нет поддерживаемых файлов", "No supported files were found in the selected folder or its subfolders."))

    def _select_output_folder(self):
        initial_dir = self.user_settings.get_last_output_dir() or self.output_dir or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку для сохранения результатов", initial_dir)
        if folder:
            self.output_dir = folder
            self.user_settings.set_last_output_dir(folder)
            self._update_output_dir_label(folder)
            self.log(f"Папка для сохранения: {folder}")

    def _update_input_dir_label(self, path: str):
        display_path = path if len(path) < 70 else f"...{path[-70:]}"
        self.lbl_input_folder.setText(self._t(f"Папка источника: {display_path}", f"Source folder: {display_path}"))
        self.lbl_input_folder.setStyleSheet(self._transparent_label_style(self._colors()["text_sub"], font_pt=9))

    def _update_output_dir_label(self, path: str):
        display_path = path if len(path) < 60 else f"...{path[-60:]}"
        self.lbl_output_folder.setText(display_path)
        self.lbl_output_folder.setStyleSheet(self._transparent_label_style(self._colors()["text_sub"]))

    def _update_llm_output_dir_label(self, path: str):
        display_path = path if len(path) < 60 else f"...{path[-60:]}"
        self.lbl_llm_output.setText(display_path)
        self.lbl_llm_output.setStyleSheet(self._transparent_label_style(self._colors()["text_sub"]))

    def _show_hf_token_dialog(self) -> bool:
        dlg = QDialog(self)
        dlg.setWindowTitle(self._t("HuggingFace токен для диаризации", "HuggingFace token for diarization"))
        dlg.setMinimumWidth(self._px(520))
        layout = QVBoxLayout(dlg)
        layout.setSpacing(self._px(10))
        info = QLabel(
            self._t(
                "<b>Диаризация спикеров требует HuggingFace токен</b><br><br>"
                "1. Создайте аккаунт на <a href='https://huggingface.co'>huggingface.co</a><br>"
                "2. Получите токен: <a href='https://huggingface.co/settings/tokens'>huggingface.co/settings/tokens</a><br>"
                "3. Примите условия доступа к моделям:<br>"
                "&nbsp;&nbsp;&nbsp;<a href='https://huggingface.co/pyannote/speaker-diarization-3.1'>pyannote/speaker-diarization-3.1</a><br>"
                "&nbsp;&nbsp;&nbsp;<a href='https://huggingface.co/pyannote/segmentation-3.0'>pyannote/segmentation-3.0</a><br><br>"
                "Вставьте ваш токен ниже (начинается с <b>hf_</b>):",
                "<b>Speaker diarization requires a HuggingFace token</b><br><br>"
                "1. Create an account at <a href='https://huggingface.co'>huggingface.co</a><br>"
                "2. Get a token: <a href='https://huggingface.co/settings/tokens'>huggingface.co/settings/tokens</a><br>"
                "3. Accept the access terms for the models:<br>"
                "&nbsp;&nbsp;&nbsp;<a href='https://huggingface.co/pyannote/speaker-diarization-3.1'>pyannote/speaker-diarization-3.1</a><br>"
                "&nbsp;&nbsp;&nbsp;<a href='https://huggingface.co/pyannote/segmentation-3.0'>pyannote/segmentation-3.0</a><br><br>"
                "Paste your token below (it starts with <b>hf_</b>):"
            )
        )
        info.setOpenExternalLinks(True)
        info.setWordWrap(True)
        layout.addWidget(info)
        token_input = QLineEdit()
        token_input.setPlaceholderText("hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        token_input.setEchoMode(QLineEdit.EchoMode.Password)
        current_token = os.getenv("HF_TOKEN", "")
        if current_token:
            token_input.setText(current_token)
        layout.addWidget(token_input)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(self._t("ОК", "OK"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self._t("Отмена", "Cancel"))
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        token = token_input.text().strip()
        if not token.startswith("hf_"):
            QMessageBox.warning(self, self._t("Неверный токен", "Invalid token"), self._t("Токен должен начинаться с 'hf_'", "The token must start with 'hf_'."))
            return False
        try:
            env_path = save_env_value("HF_TOKEN", token)
            self.log(f"Токен сохранён: {env_path}")
        except Exception as exc:
            QMessageBox.warning(
                self,
                self._t("Не удалось сохранить токен", "Could not save token"),
                str(exc),
            )
            return False
        return True

    def _edit_hf_token(self):
        """Открыть диалог токена независимо от его текущего состояния."""
        self._show_hf_token_dialog()
