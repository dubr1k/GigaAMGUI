"""
Главное окно приложения GigaAM v3 Transcriber на PyQt6
Строгий профессиональный дизайн без ярких цветов
"""

import os
import json
import shutil
import sys
import threading
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None

from PyQt6.QtCore import QByteArray, QEvent, QLibraryInfo, QObject, Qt, QTimer, QTranslator, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QKeySequence,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..config import (
    APP_TITLE, MEDIA_EXTENSIONS, OUTPUT_FORMATS, STATS_FILE,
    LLM_API_URL, LLM_API_KEY, LLM_MODEL, LLM_TEMPERATURE,
    save_env_value, user_config_dir,
)
from ..core import ModelLoader
from .asr_backend_dialog import ASRBackendDialog, is_mlx_supported
from ..utils import (
    AppLogger, MediaDownloader, ProcessingStats,
    TimeFormatter, UserSettings,
)
from ..core.progress import ProgressEvent
from ..services import transcription_service
from .download_mixin import DownloadMixin
from .files_mixin import FilesMixin
from .i18n_mixin import I18nMixin
from .llm_mixin import LlmMixin
from .llm_ui_mixin import LlmUiMixin
from .processing_mixin import ProcessingMixin
from .theme_mixin import ThemeMixin
from .ui_build_mixin import UiBuildMixin

_BASE_FONT_PT = 12.0
_MIN_UI_SCALE = 0.85
_MAX_UI_SCALE = 1.75
_INSTANCE_LOCK_NAME = "instance.lock"
_OPEN_REQUESTS_NAME = "open_requests.jsonl"

SUMMARY_PROMPT = (
    "Ты аналитик встреч и голосовых сообщений. Сделай сильную, плотную и полезную выжимку транскрипта на русском языке. "
    "Убери повторы, слова-паразиты и шум распознавания. Сохрани только смысл. "
    "\n\nСтруктура ответа:" 
    "\n1. Краткое резюме в 3-6 пунктах." 
    "\n2. Ключевые договоренности и решения." 
    "\n3. Важные факты, цифры, сроки, имена и роли — если они есть." 
    "\n4. Риски, спорные места или открытые вопросы — если они есть." 
    "\n\nПиши четко, по делу, без воды. Если часть информации в транскрипте неясна, пометь это явно и не выдумывай."
)

TASKS_PROMPT = (
    "Ты project manager assistant. Из транскрипта выдели только конкретные задачи и оформи их в максимально рабочем виде на русском языке. "
    "Игнорируй рассуждения, повторы и фоновые фразы. Не выдумывай задачи, которых нет в тексте. "
    "\n\nДля каждой задачи укажи:" 
    "\n- Что нужно сделать" 
    "\n- Кто ответственный / исполнитель, если это можно понять" 
    "\n- Срок, дедлайн или ориентир по времени, если упомянут" 
    "\n- Контекст или комментарий, если он важен" 
    "\n- Приоритет, если он читается из разговора" 
    "\n\nСначала дай список задач. Затем отдельным коротким блоком выведи: " 
    "«Открытые вопросы / неясности». Если задач нет, напиши: «Явных задач не найдено»."
)


def _read_ui_scale() -> float:
    raw_value = os.getenv("GIGAAM_UI_SCALE", "1").strip().replace(",", ".")
    try:
        scale = float(raw_value)
    except ValueError:
        scale = 1.0
    return max(_MIN_UI_SCALE, min(_MAX_UI_SCALE, scale))


def _format_css_number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


class WorkerSignals(QObject):
    """Сигналы для потока обработки"""
    log_message = pyqtSignal(str)
    progress_update = pyqtSignal(int)
    file_progress_update = pyqtSignal(int)
    current_file_info = pyqtSignal(str)
    processing_finished = pyqtSignal(bool, str)
    stage_update = pyqtSignal(object)
    download_progress = pyqtSignal(int)
    download_finished = pyqtSignal(list)
    download_failed = pyqtSignal(str)
    llm_finished = pyqtSignal(bool, str, str)


class GigaApplication(QApplication):
    """QApplication with macOS Finder/Dock open-file event support."""

    file_open_requested = pyqtSignal(list)

    def __init__(self, argv):
        super().__init__(argv)
        self._pending_open_paths = []

    def event(self, event):
        if event.type() == QEvent.Type.FileOpen:
            path = ""
            try:
                url = event.url()
                if url.isLocalFile():
                    path = url.toLocalFile()
            except Exception:
                path = ""
            if not path:
                try:
                    path = event.file()
                except Exception:
                    path = ""
            if path:
                self._pending_open_paths.append(path)
                self.file_open_requested.emit([path])
            return True
        return super().event(event)

    def take_pending_open_paths(self):
        paths = self._pending_open_paths[:]
        self._pending_open_paths.clear()
        return paths


class GigaTranscriberQtApp(
    LlmMixin, LlmUiMixin, DownloadMixin, ProcessingMixin, FilesMixin,
    I18nMixin, ThemeMixin, UiBuildMixin, QMainWindow,
):
    """Главное окно приложения для транскрибации на PyQt6"""

    def __init__(self):
        super().__init__()

        self.files_to_process = []
        self.output_dir = ""
        self.input_dir = ""
        self.is_processing = False
        self._last_generated_transcript_files = []
        self._cancel_requested = False
        self.start_time = None
        self.files_processed = 0
        self.total_files = 0
        self.time_spent = 0
        self.current_file_start_time = 0
        self.current_stage = None
        self.current_stage_progress = 0.0
        self.current_stage_file_progress = 0.0
        self.current_stage_is_indeterminate = False
        self._stage_start_time = 0.0
        self._current_filename = ""
        self.is_downloading = False
        self.start_processing_after_download = False
        self._last_result_dir = ""

        self.transcript_files_for_llm = []
        self.llm_output_dir = ""
        self.llm_transcript_dir = os.path.expanduser("~")
        self.is_llm_processing = False
        self.llm_last_result_text = ""
        self.llm_last_result_name = "llm_result"

        self.enable_diarization = False
        self.num_speakers = None
        self._diarization_prompt_open = False

        self.output_formats = {
            'txt': True,
            'txt_timecodes': True,
            'txt_diarize': False,
            'txt_diarize_timecodes': False,
            'md': False,
            'srt': False,
            'vtt': False,
        }

        self.app_logger = AppLogger()
        self.app_logger.log_session_start()
        self.model_loader = ModelLoader()
        self.stats = ProcessingStats(STATS_FILE)
        self.time_formatter = TimeFormatter()
        self.user_settings = UserSettings()
        self.media_downloader = MediaDownloader()

        self._theme = self.user_settings.settings.get("theme", "dark")
        self._lang = self.user_settings.settings.get("language", "ru")
        self._ui_scale = self._effective_ui_scale()

        self.signals = WorkerSignals()
        self.signals.log_message.connect(self._append_log)
        self.signals.progress_update.connect(self._update_total_progress)
        self.signals.file_progress_update.connect(self._update_file_progress)
        self.signals.current_file_info.connect(self._update_current_file_info)
        self.signals.processing_finished.connect(self._on_processing_finished)
        self.signals.stage_update.connect(self._on_stage_update)
        self.signals.download_progress.connect(self._update_download_progress)
        self.signals.download_finished.connect(self._on_download_finished)
        self.signals.download_failed.connect(self._on_download_failed)
        self.signals.llm_finished.connect(self._on_llm_finished)

        saved_output_dir = self.user_settings.get_last_output_dir()
        saved_input_dir = self.user_settings.get_last_files_dir()
        saved_llm_output_dir = self.user_settings.get_value("llm_output_dir", "")
        saved_llm_transcript_dir = self.user_settings.get_value("llm_transcript_dir", "")

        if saved_output_dir:
            self.output_dir = saved_output_dir
        if saved_input_dir:
            self.input_dir = saved_input_dir
        if saved_llm_output_dir and os.path.isdir(saved_llm_output_dir):
            self.llm_output_dir = saved_llm_output_dir
        elif saved_output_dir:
            self.llm_output_dir = saved_output_dir
        if saved_llm_transcript_dir and os.path.isdir(saved_llm_transcript_dir):
            self.llm_transcript_dir = saved_llm_transcript_dir
        elif saved_input_dir:
            self.llm_transcript_dir = saved_input_dir

        self._init_ui()
        self._restore_ui_settings()
        self.setAcceptDrops(True)

        if saved_output_dir:
            self._update_output_dir_label(saved_output_dir)
        if saved_input_dir:
            self._update_input_dir_label(saved_input_dir)
        if self.llm_output_dir:
            self._update_llm_output_dir_label(self.llm_output_dir)

        self.app_logger.cleanup_old_logs()

    # ──────────────────────────────────────────────────────────────
    # Темы
    # ──────────────────────────────────────────────────────────────

    _LIGHT = {
        "bg":        "#f5f6f8",
        "bg_card":   "#ffffff",
        "border":    "#dde0e6",
        "text":      "#1a1a1a",
        "text_sub":  "#374151",
        "text_mute": "#9ca3af",
        "text_mute2":"#6b7280",
        "btn_bg":    "#ffffff",
        "btn_border":"#d1d5db",
        "btn_text":  "#374151",
        "btn_hover_bg":    "#f0f4ff",
        "btn_hover_border":"#3b82f6",
        "btn_hover_text":  "#1d4ed8",
        "accent":    "#3b82f6",
        "accent2":   "#2563eb",
        "accent3":   "#1d4ed8",
        "accent_dis":"#93c5fd",
        "clear_bg":  "#f3f4f6",
        "clear_text":"#6b7280",
        "clear_border":"#e5e7eb",
        "clear_hover_bg":"#fee2e2",
        "clear_hover_border":"#fca5a5",
        "clear_hover_text":"#dc2626",
        "input_bg":  "#ffffff",
        "input_sel": "#bfdbfe",
        "input_dis": "#f9fafb",
        "input_dis_text":"#9ca3af",
        "progress_bg":"#e5e7eb",
        "progress_chunk":"#3b82f6",
        "progress_chunk2":"#2563eb",
        "tab_bg":    "#e8eaee",
        "tab_text":  "#555",
        "tab_sel_bg":"#f5f6f8",
        "tab_sel_text":"#1a1a1a",
        "tab_accent":"#3b82f6",
        "tab_hover": "#dde1ea",
        "scroll_bg": "#f1f2f4",
        "scroll_handle":"#cbd5e1",
        "scroll_handle_hover":"#94a3b8",
        "status_bg": "#f3f4f6",
        "status_text":"#374151",
        "theme_btn": "🌙",
    }

    _DARK = {
        "bg":        "#1e1e21",
        "bg_card":   "#2d2d30",
        "border":    "#3e3e42",
        "text":      "#e8e8e8",
        "text_sub":  "#c8c8c8",
        "text_mute": "#6b6b6b",
        "text_mute2":"#888888",
        "btn_bg":    "#3a3a3d",
        "btn_border":"#4a4a4e",
        "btn_text":  "#d0d0d0",
        "btn_hover_bg":    "#45455a",
        "btn_hover_border":"#5b7ee5",
        "btn_hover_text":  "#a8c4ff",
        "accent":    "#4f7de8",
        "accent2":   "#3a6ad4",
        "accent3":   "#2c57be",
        "accent_dis":"#2c3f6b",
        "clear_bg":  "#35353a",
        "clear_text":"#888888",
        "clear_border":"#3e3e44",
        "clear_hover_bg":"#4a2020",
        "clear_hover_border":"#8b3a3a",
        "clear_hover_text":"#e05050",
        "input_bg":  "#252528",
        "input_sel": "#1a3a6b",
        "input_dis": "#222225",
        "input_dis_text":"#555558",
        "progress_bg":"#303035",
        "progress_chunk":"#4f7de8",
        "progress_chunk2":"#3a6ad4",
        "tab_bg":    "#2a2a2d",
        "tab_text":  "#909090",
        "tab_sel_bg":"#1e1e21",
        "tab_sel_text":"#e8e8e8",
        "tab_accent":"#4f7de8",
        "tab_hover": "#333338",
        "scroll_bg": "#252528",
        "scroll_handle":"#4a4a50",
        "scroll_handle_hover":"#6a6a72",
        "status_bg": "#252528",
        "status_text":"#c0c0c0",
        "theme_btn": "☀️",
    }

    def _colors(self):
        return self._DARK if self._theme == "dark" else self._LIGHT

    def _effective_ui_scale(self) -> float:
        app = QApplication.instance()
        font_pt = app.font().pointSizeF() if app else _BASE_FONT_PT
        if font_pt <= 0:
            font_pt = _BASE_FONT_PT
        font_scale = max(_MIN_UI_SCALE, min(_MAX_UI_SCALE, font_pt / _BASE_FONT_PT))
        ui_scale = font_scale * _read_ui_scale()
        return round(max(_MIN_UI_SCALE, min(_MAX_UI_SCALE, ui_scale)), 4)

    def _px(self, value: int | float) -> int:
        return max(1, int(round(value * self._ui_scale)))

    def _pt(self, value: int | float) -> float:
        return round(value * self._ui_scale, 2)

    def _pt_css(self, value: int | float) -> str:
        return _format_css_number(self._pt(value))

    def _transparent_label_style(
        self,
        color: str,
        font_pt: int | float | None = None,
        font_weight: str | None = None,
    ) -> str:
        parts = ["background: transparent", f"color: {color}"]
        if font_pt is not None:
            parts.append(f"font-size: {self._pt_css(font_pt)}pt")
        if font_weight:
            parts.append(f"font-weight: {font_weight}")
        return "; ".join(parts) + ";"

    def _font(self, point_size: int | float, weight: QFont.Weight = QFont.Weight.Normal, fixed: bool = False) -> QFont:
        font_kind = QFontDatabase.SystemFont.FixedFont if fixed else QFontDatabase.SystemFont.GeneralFont
        font = QFontDatabase.systemFont(font_kind)
        font.setPointSizeF(self._pt(point_size))
        font.setWeight(weight)
        return font

    def _tab_min_width(self, labels: tuple[str, ...]) -> int:
        metrics = QFontMetrics(self._font(11, QFont.Weight.Bold))
        text_width = max(metrics.horizontalAdvance(label) for label in labels)
        return text_width + self._px(36)

    def _label_column_width(self, labels: tuple[str, ...], extra: int = 6) -> int:
        """Ширина колонки, достаточная для самого длинного лейбла из набора.

        Используется, чтобы поля формы (значения справа от лейблов) всегда
        начинались с одной и той же координаты X, даже если тексты лейблов
        разной длины ("Провайдер:", "Модель:", "API URL:" и т.д.).
        """
        metrics = QFontMetrics(self._font(10))
        text_width = max(metrics.horizontalAdvance(label) for label in labels)
        return text_width + self._px(extra)

    def _form_label(self, text: str, column_width: int) -> QLabel:
        lbl = QLabel(text)
        lbl.setFixedWidth(column_width)
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return lbl

    def _toggle_theme(self):
        self._theme = "dark" if self._theme == "light" else "light"
        self.user_settings.settings["theme"] = self._theme
        self.user_settings._save_settings()
        self._apply_theme()
        self._btn_theme.setText(self._colors()["theme_btn"])





    def _select_asr_backend(self):
        if self.is_processing:
            QMessageBox.information(
                self,
                self._t("Смена backend", "Backend change"),
                self._t(
                    "Дождитесь завершения обработки перед сменой backend.",
                    "Wait for processing to finish before changing backend.",
                ),
            )
            return

        selected = ASRBackendDialog.pick(
            self,
            current_backend=self.model_loader.requested_backend,
            mlx_supported=is_mlx_supported(),
        )

        if not selected or selected == self.model_loader.requested_backend:
            return

        self.model_loader.configure_backend(selected)
        self.user_settings.set_value("asr_backend", selected)
        self.log(f"Выбран ASR backend: {selected}" if self._lang == "ru" else f"ASR backend selected: {selected}")

    def _change_device(self):
        """Смена вычислительного устройства (CPU / GPU / GPU 50xx) из меню."""
        from .device_dialog import change_device_interactive
        from PyQt6.QtWidgets import QMessageBox

        if self.is_processing:
            QMessageBox.information(
                self,
                self._t("Устройство", "Device"),
                self._t("Дождитесь окончания обработки перед сменой устройства.", "Wait for processing to finish before changing the device."),
            )
            return

        changed = change_device_interactive(self)
        if changed:
            # Модель уже загружена под старую сборку torch — нужен перезапуск.
            self.model_loader.unload()

    # ──────────────────────────────────────────────────────────────
    # UI
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _is_headless() -> bool:
        app = QApplication.instance()
        return bool(app) and app.platformName() in ("offscreen", "minimal")

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
        self._set_status(self._t("Журнал очищен", "Log cleared"))

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









    def log(self, message: str):
        message = self._translate_runtime_text(message)
        self.signals.log_message.emit(message)
        self.app_logger.get_logger().info(message)

    def _append_log(self, message: str):
        self.log_text.append(f">> {message}")















    def _restore_ui_settings(self):
        saved_formats = self.user_settings.get_value("output_formats", {}) or {}
        for fmt, cb in self.format_checkboxes.items():
            cb.blockSignals(True)
            cb.setChecked(bool(saved_formats.get(fmt, self.output_formats.get(fmt, False))))
            cb.blockSignals(False)
            self.output_formats[fmt] = cb.isChecked()

        diarization_enabled = bool(self.user_settings.get_value("enable_diarization", False))
        num_speakers = int(self.user_settings.get_value("num_speakers", 0) or 0)
        self.cb_diarization.blockSignals(True)
        self.cb_diarization.setChecked(diarization_enabled)
        self.cb_diarization.blockSignals(False)
        self.enable_diarization = diarization_enabled
        self.entry_num_speakers.setEnabled(diarization_enabled)
        self.entry_num_speakers.setValue(num_speakers)
        for fmt in ('txt_diarize', 'txt_diarize_timecodes'):
            cb = self.format_checkboxes.get(fmt)
            if cb:
                cb.setEnabled(diarization_enabled)

        provider = self._normalize_llm_provider(self.user_settings.get_value("llm_provider", "API"))
        display_provider = ("Другое" if self._lang == "ru" else "Other") if provider == "Other" else provider
        index = self.combo_llm_provider.findText(display_provider)
        self.combo_llm_provider.setCurrentIndex(index if index >= 0 else 0)
        self.entry_llm_api_url.setText(self.user_settings.get_value("llm_api_url", LLM_API_URL))
        self.entry_llm_api_key.setText(LLM_API_KEY)
        self.entry_llm_model.setText(self.user_settings.get_value("llm_model", LLM_MODEL))
        self.entry_llm_temperature.setText(str(self.user_settings.get_value("llm_temperature", LLM_TEMPERATURE)))
        self.entry_llm_claude_path.setText(self.user_settings.get_value("llm_claude_path", shutil.which("claude") or "claude"))
        self.entry_llm_claude_args.setText(self.user_settings.get_value("llm_claude_args", ""))
        self.entry_llm_codex_path.setText(self.user_settings.get_value("llm_codex_path", shutil.which("codex") or "codex"))
        self.entry_llm_codex_args.setText(self.user_settings.get_value("llm_codex_args", ""))
        self.entry_llm_opencode_path.setText(self.user_settings.get_value("llm_opencode_path", shutil.which("opencode") or "opencode"))
        self.entry_llm_opencode_args.setText(self.user_settings.get_value("llm_opencode_args", ""))
        self.entry_llm_pi_path.setText(self.user_settings.get_value("llm_pi_path", shutil.which("pi") or "pi"))
        self.entry_llm_pi_provider.setText(self.user_settings.get_value("llm_pi_provider", ""))
        self.entry_llm_pi_args.setText(self.user_settings.get_value("llm_pi_args", ""))
        self.entry_llm_other_path.setText(self.user_settings.get_value("llm_other_path", ""))
        self.entry_llm_other_args.setText(self.user_settings.get_value("llm_other_args", ""))
        self._update_llm_provider_fields(self.combo_llm_provider.currentText())
        self.txt_llm_summary_prompt.setPlainText(self.user_settings.get_value("llm_summary_prompt", SUMMARY_PROMPT))
        self.txt_llm_tasks_prompt.setPlainText(self.user_settings.get_value("llm_tasks_prompt", TASKS_PROMPT))
        self.txt_llm_custom_prompt.setPlainText(self.user_settings.get_value("llm_custom_prompt", ""))
        self.txt_llm_transcript.setPlainText(self.user_settings.get_value("llm_manual_transcript", ""))

        tab_index = int(self.user_settings.get_value("active_tab_index", 0) or 0)
        if 0 <= tab_index < self.tabs.count():
            self.tabs.setCurrentIndex(tab_index)

        saved_audio_files = self.user_settings.get_value("last_selected_audio_files", []) or []
        self.files_to_process = [path for path in saved_audio_files if os.path.isfile(path)]
        if self.files_to_process:
            self._refresh_files_list()

        saved_llm_actions = self.user_settings.get_value("llm_actions", {}) or {}
        for key, cb in self.llm_action_checkboxes.items():
            cb.setChecked(bool(saved_llm_actions.get(key, cb.isChecked())))

        saved_llm_exports = self.user_settings.get_value("llm_export_formats", {}) or {}
        for key, cb in self.llm_export_checkboxes.items():
            cb.setChecked(bool(saved_llm_exports.get(key, cb.isChecked())))

        saved_asr_backend = self.user_settings.get_value("asr_backend", "")
        if isinstance(saved_asr_backend, str) and saved_asr_backend:
            try:
                self.model_loader.configure_backend(saved_asr_backend)
            except Exception:
                self.model_loader.configure_backend("auto")

        saved_llm_files = self.user_settings.get_value("last_selected_transcript_files", []) or []
        self.transcript_files_for_llm = [path for path in saved_llm_files if os.path.isfile(path)]
        self._refresh_llm_files_list()

    def _save_ui_settings(self):
        self.user_settings.set_value("output_formats", self.output_formats)
        self.user_settings.set_value("enable_diarization", self.cb_diarization.isChecked())
        self.user_settings.set_value("num_speakers", self.entry_num_speakers.value())
        self.user_settings.set_value("asr_backend", self.model_loader.requested_backend)
        self.user_settings.set_value("llm_provider", self._normalize_llm_provider(self.combo_llm_provider.currentText()))
        self.user_settings.set_value("llm_api_url", self.entry_llm_api_url.text().strip())
        llm_api_key = self.entry_llm_api_key.text().strip()
        if llm_api_key:
            try:
                save_env_value("LLM_API_KEY", llm_api_key)
            except OSError as exc:
                self.log(f"Не удалось сохранить LLM API key: {exc}")
        self.user_settings.set_value("llm_model", self.entry_llm_model.text().strip())
        self.user_settings.set_value("llm_temperature", self.entry_llm_temperature.text().strip())
        self.user_settings.set_value("llm_claude_path", self.entry_llm_claude_path.text().strip())
        self.user_settings.set_value("llm_claude_args", self.entry_llm_claude_args.text().strip())
        self.user_settings.set_value("llm_codex_path", self.entry_llm_codex_path.text().strip())
        self.user_settings.set_value("llm_codex_args", self.entry_llm_codex_args.text().strip())
        self.user_settings.set_value("llm_opencode_path", self.entry_llm_opencode_path.text().strip())
        self.user_settings.set_value("llm_opencode_args", self.entry_llm_opencode_args.text().strip())
        self.user_settings.set_value("llm_pi_path", self.entry_llm_pi_path.text().strip())
        self.user_settings.set_value("llm_pi_provider", self.entry_llm_pi_provider.text().strip())
        self.user_settings.set_value("llm_pi_args", self.entry_llm_pi_args.text().strip())
        self.user_settings.set_value("llm_other_path", self.entry_llm_other_path.text().strip())
        self.user_settings.set_value("llm_other_args", self.entry_llm_other_args.text().strip())
        self.user_settings.set_value("llm_summary_prompt", self.txt_llm_summary_prompt.toPlainText())
        self.user_settings.set_value("llm_tasks_prompt", self.txt_llm_tasks_prompt.toPlainText())
        self.user_settings.set_value("llm_custom_prompt", self.txt_llm_custom_prompt.toPlainText())
        self.user_settings.set_value("llm_manual_transcript", self.txt_llm_transcript.toPlainText())
        self.user_settings.set_value("active_tab_index", self.tabs.currentIndex())
        self.user_settings.set_value("llm_actions", {k: cb.isChecked() for k, cb in self.llm_action_checkboxes.items()})
        self.user_settings.set_value("llm_export_formats", {k: cb.isChecked() for k, cb in self.llm_export_checkboxes.items()})
        self.user_settings.set_value("last_selected_audio_files", [p for p in self.files_to_process if os.path.isfile(p)])
        self.user_settings.set_value("last_selected_transcript_files", [p for p in self.transcript_files_for_llm if os.path.isfile(p)])
        if self.llm_output_dir:
            self.user_settings.set_value("llm_output_dir", self.llm_output_dir)
        if self.llm_transcript_dir:
            self.user_settings.set_value("llm_transcript_dir", self.llm_transcript_dir)

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
        self.user_settings.set_value("last_selected_transcript_files", [p for p in self.transcript_files_for_llm if os.path.isfile(p)])

    def _clear_llm_files_list(self):
        if self.is_llm_processing or not self.transcript_files_for_llm:
            return
        self.transcript_files_for_llm = []
        self._refresh_llm_files_list()
        self.user_settings.set_value("last_selected_transcript_files", [])

    def _select_llm_transcript_files(self):
        initial_dir = self.user_settings.get_value("llm_transcript_dir", self.llm_transcript_dir)
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Выберите транскрипты",
            initial_dir,
            "Транскрипты (*.txt *.md *.srt *.vtt);;Текстовые файлы (*.txt *.md);;Все файлы (*.*)"
        )
        if files:
            self.transcript_files_for_llm = files
            folder = os.path.dirname(files[0])
            self.llm_transcript_dir = folder
            if not self.llm_output_dir:
                self.llm_output_dir = folder
                self._update_llm_output_dir_label(folder)
            self.user_settings.set_value("llm_transcript_dir", folder)
            self.user_settings.set_value("last_selected_transcript_files", files)
            self._refresh_llm_files_list()
            self.lbl_llm_status.setText(self._t("Транскрипты готовы к LLM-обработке", "Transcripts are ready for LLM processing"))

    def _select_llm_output_folder(self):
        initial_dir = self.llm_output_dir or self.output_dir or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку для сохранения LLM-результатов", initial_dir)
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
        self._refresh_llm_files_list()
        self._clear_llm_result()
        self.user_settings.set_value("last_selected_transcript_files", [])
        self.user_settings.set_value("llm_manual_transcript", "")

    def _selected_llm_modes(self):
        modes = []
        if self.llm_action_checkboxes["summary"].isChecked():
            prompt = self.txt_llm_summary_prompt.toPlainText().strip() or SUMMARY_PROMPT
            modes.append(("summary", "Выжимка", prompt))
        if self.llm_action_checkboxes["tasks"].isChecked():
            prompt = self.txt_llm_tasks_prompt.toPlainText().strip() or TASKS_PROMPT
            modes.append(("tasks", "Задачи", prompt))
        if self.llm_action_checkboxes["custom"].isChecked():
            custom_prompt = self.txt_llm_custom_prompt.toPlainText().strip()
            if not custom_prompt:
                raise ValueError("Для режима «Свой промпт» укажите пользовательский промпт в меню «Настройки → LLM API…»")
            modes.append(("custom", "Свой промпт", custom_prompt))
        if not modes:
            raise ValueError("Выберите хотя бы один чекбокс в блоке «Что делать»")
        return modes

    def _selected_llm_export_formats(self):
        formats = [key for key, cb in self.llm_export_checkboxes.items() if cb.isChecked()]
        if not formats:
            raise ValueError("Выберите хотя бы один формат сохранения результата")
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
        self.lbl_llm_status.setText("Идет LLM-обработка...")
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
        except ValueError:
            raise ValueError("Temperature должно быть числом")
        if not 0 <= temperature <= 2:
            raise ValueError("Temperature должно быть в диапазоне 0..2")

        if provider == "API":
            if not api_url:
                raise ValueError("Укажите API URL")
            if not api_key:
                raise ValueError("Укажите API Key")
            if not model:
                raise ValueError("Укажите модель")
        elif provider == "Claude Code":
            claude_path = self.entry_llm_claude_path.text().strip() or "claude"
            if not (shutil.which(claude_path) or os.path.isfile(claude_path)):
                raise ValueError(f"Не найден Claude Code: {claude_path}")
        elif provider == "Codex":
            codex_path = self.entry_llm_codex_path.text().strip() or "codex"
            if not (shutil.which(codex_path) or os.path.isfile(codex_path)):
                raise ValueError(f"Не найден Codex: {codex_path}")
        elif provider == "OpenCode":
            opencode_path = self.entry_llm_opencode_path.text().strip() or "opencode"
            if not (shutil.which(opencode_path) or os.path.isfile(opencode_path)):
                raise ValueError(f"Не найден OpenCode: {opencode_path}")
        elif provider == "Pi":
            pi_path = self.entry_llm_pi_path.text().strip() or "pi"
            if not (shutil.which(pi_path) or os.path.isfile(pi_path)):
                raise ValueError(f"Не найден Pi: {pi_path}")
        elif self._normalize_llm_provider(provider) == "Other":
            other_path = self.entry_llm_other_path.text().strip()
            if not other_path:
                raise ValueError(self._t("Укажите команду для провайдера «Другое»", "Specify a command for the 'Other' provider"))
            if not (shutil.which(other_path) or os.path.isfile(other_path)):
                raise ValueError(f"Не найдена команда: {other_path}")
        return {
            "provider": provider,
            "api_url": api_url,
            "api_key": api_key,
            "model": model,
            "temperature": temperature,
            "claude_path": self.entry_llm_claude_path.text().strip() or "claude",
            "claude_args": self.entry_llm_claude_args.text().strip(),
            "codex_path": self.entry_llm_codex_path.text().strip() or "codex",
            "codex_args": self.entry_llm_codex_args.text().strip(),
            "opencode_path": self.entry_llm_opencode_path.text().strip() or "opencode",
            "opencode_args": self.entry_llm_opencode_args.text().strip(),
            "pi_path": self.entry_llm_pi_path.text().strip() or "pi",
            "pi_provider": self.entry_llm_pi_provider.text().strip(),
            "pi_args": self.entry_llm_pi_args.text().strip(),
            "other_path": self.entry_llm_other_path.text().strip(),
            "other_args": self.entry_llm_other_args.text().strip(),
        }

    def _collect_llm_inputs(self):
        manual_text = self.txt_llm_transcript.toPlainText().strip()
        items = []
        if manual_text:
            base_name = "manual_transcript"
            if self.transcript_files_for_llm:
                base_name = Path(self.transcript_files_for_llm[0]).stem
            items.append({"name": base_name, "text": manual_text, "source_path": self.transcript_files_for_llm[0] if self.transcript_files_for_llm else None})
        for path in self.transcript_files_for_llm:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read().strip()
            except OSError:
                continue
            if text:
                items.append({"name": Path(path).stem, "text": text, "source_path": path})
        if not items:
            raise ValueError("Выберите хотя бы один транскрипт или вставьте текст вручную")
        return items

    # ──────────────────────────────────────────────────────────────
    # Обработка файлов
    # ──────────────────────────────────────────────────────────────

def _argv_open_paths(argv: list) -> list:
    paths = []
    for arg in argv[1:]:
        if arg.startswith("-psn_"):
            continue
        path = os.path.abspath(os.path.expanduser(arg))
        if os.path.exists(path):
            paths.append(path)
    return paths


def _instance_lock_path() -> Path:
    return user_config_dir() / _INSTANCE_LOCK_NAME


def _open_requests_path() -> Path:
    return user_config_dir() / _OPEN_REQUESTS_NAME


def _try_acquire_instance_lock():
    lock_path = _instance_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("w", encoding="utf-8")
    if fcntl is None:
        return lock_file
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return None
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


def _queue_open_request(paths: list) -> None:
    queue_path = _open_requests_path()
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"paths": paths, "pid": os.getpid(), "time": time.time()}
    with queue_path.open("a", encoding="utf-8") as queue:
        queue.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _install_open_request_poller(window: GigaTranscriberQtApp):
    queue_path = _open_requests_path()
    timer = QTimer(window)

    def poll_requests():
        if not queue_path.exists():
            return
        try:
            lines = queue_path.read_text(encoding="utf-8").splitlines()
            queue_path.write_text("", encoding="utf-8")
        except OSError as exc:
            window.log(f"Не удалось прочитать очередь open requests: {exc}")
            return
        for line in lines:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            paths = payload.get("paths") or []
            if isinstance(paths, list):
                window.open_paths_from_system(paths, append=True)

    timer.timeout.connect(poll_requests)
    timer.start(500)
    window._open_request_poller = timer
    QTimer.singleShot(0, poll_requests)
    return timer


def run_qt_app(app=None):
    """Запускает приложение на PyQt6.

    app: уже созданный QApplication (используется на этапе выбора устройства).
    """
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('GigaAM.Transcriber.v3')
        except Exception:
            pass

    app = app or QApplication.instance() or GigaApplication(sys.argv)
    app.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont))

    initial_open_paths = _argv_open_paths(sys.argv)
    instance_lock = getattr(app, "_gigaam_instance_lock_file", None) or _try_acquire_instance_lock()
    if instance_lock is None:
        _queue_open_request(initial_open_paths)
        sys.exit(0)

    icon_path = os.path.join(
        getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
        'icon.ico'
    )
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    window = GigaTranscriberQtApp()
    window._instance_lock_file = instance_lock
    _install_open_request_poller(window)
    if isinstance(app, GigaApplication):
        app.file_open_requested.connect(lambda paths: window.open_paths_from_system(paths, append=True))
    window.show()
    if initial_open_paths:
        QTimer.singleShot(0, lambda: window.open_paths_from_system(initial_open_paths, append=True))
    if isinstance(app, GigaApplication):
        pending_open_paths = app.take_pending_open_paths()
        if pending_open_paths:
            QTimer.singleShot(0, lambda paths=pending_open_paths: window.open_paths_from_system(paths, append=True))

    if sys.platform == 'win32' and os.path.exists(icon_path):
        def _set_win32_icon():
            try:
                import ctypes
                hwnd = int(window.winId())
                hicon = ctypes.windll.user32.LoadImageW(None, icon_path, 1, 0, 0, 0x10 | 0x40)
                if hicon:
                    ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 0, hicon)
                    ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 1, hicon)
            except Exception:
                pass
        QTimer.singleShot(100, _set_win32_icon)

    sys.exit(app.exec())


if __name__ == "__main__":
    run_qt_app()
