"""
Главное окно приложения GigaAM v3 Transcriber на PyQt6
Строгий профессиональный дизайн без ярких цветов
"""

import os
import sys
import threading
import time

from PyQt6.QtCore import QByteArray, QObject, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPalette,
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

from ..config import APP_TITLE, MEDIA_EXTENSIONS, OUTPUT_FORMATS, STATS_FILE
from ..core import ModelLoader, TranscriptionProcessor
from ..utils import AppLogger, AudioConverter, MediaDownloader, ProcessingStats, TimeFormatter, UserSettings

_BASE_FONT_PT = 12.0
_MIN_UI_SCALE = 0.85
_MAX_UI_SCALE = 1.75


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
    stage_update = pyqtSignal(str, float)
    download_progress = pyqtSignal(int)
    download_finished = pyqtSignal(list)
    download_failed = pyqtSignal(str)


class GigaTranscriberQtApp(QMainWindow):
    """Главное окно приложения для транскрибации на PyQt6"""

    def __init__(self):
        super().__init__()

        self.files_to_process = []
        self.output_dir = ""
        self.input_dir = ""
        self.is_processing = False
        self._cancel_requested = False
        self.start_time = None
        self.files_processed = 0
        self.total_files = 0
        self.file_estimates = {}
        self.total_estimated_time = 0
        self.time_spent = 0
        self.current_file_start_time = 0
        self.current_stage = None
        self.current_stage_progress = 0.0
        self._stage_start_time = 0.0
        self._current_filename = ""
        self.is_downloading = False
        self.start_processing_after_download = False
        self._last_result_dir = ""

        self.enable_diarization = False
        self.num_speakers = None

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

        self.progress_timer = QTimer()
        self.progress_timer.timeout.connect(self._update_progress_display)

        saved_output_dir = self.user_settings.get_last_output_dir()
        saved_input_dir = self.user_settings.get_last_files_dir()

        if saved_output_dir:
            self.output_dir = saved_output_dir
        if saved_input_dir:
            self.input_dir = saved_input_dir

        self._init_ui()
        self.setAcceptDrops(True)

        if saved_output_dir:
            self._update_output_dir_label(saved_output_dir)
        if saved_input_dir:
            self._update_input_dir_label(saved_input_dir)

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

    def _toggle_theme(self):
        self._theme = "dark" if self._theme == "light" else "light"
        self.user_settings.settings["theme"] = self._theme
        self.user_settings._save_settings()
        self._apply_theme()
        self._btn_theme.setText(self._colors()["theme_btn"])

    def _change_device(self):
        """Смена вычислительного устройства (CPU / GPU / GPU 50xx) из меню."""
        from .device_dialog import change_device_interactive
        from PyQt6.QtWidgets import QMessageBox

        if self.is_processing:
            QMessageBox.information(
                self, "Устройство",
                "Дождитесь окончания обработки перед сменой устройства.",
            )
            return

        changed = change_device_interactive(self)
        if changed:
            # Модель уже загружена под старую сборку torch — нужен перезапуск.
            self.model_loader.unload()

    def _apply_theme(self):
        c = self._colors()
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window,          QColor(c["bg"]))
        palette.setColor(QPalette.ColorRole.WindowText,      QColor(c["text"]))
        palette.setColor(QPalette.ColorRole.Base,            QColor(c["input_bg"]))
        palette.setColor(QPalette.ColorRole.AlternateBase,   QColor(c["bg_card"]))
        palette.setColor(QPalette.ColorRole.Text,            QColor(c["text"]))
        palette.setColor(QPalette.ColorRole.Button,          QColor(c["btn_bg"]))
        palette.setColor(QPalette.ColorRole.ButtonText,      QColor(c["btn_text"]))
        palette.setColor(QPalette.ColorRole.Highlight,       QColor(c["accent"]))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        self.setPalette(palette)

        r = c["progress_chunk"]
        r2 = c["progress_chunk2"]
        rad_f = self._px(11)
        rad_s = self._px(8)
        tab_min_width = self._tab_min_width(("Обработка", "Журнал обработки"))

        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background-color: {c["bg"]};
                color: {c["text"]};
            }}
            QScrollArea {{
                background-color: {c["bg"]};
                border: none;
            }}
            QTabWidget::pane {{
                border: 1px solid {c["border"]};
                border-radius: {self._px(6)}px;
                background-color: {c["bg"]};
            }}
            QTabBar::tab {{
                background-color: {c["tab_bg"]};
                color: {c["tab_text"]};
                border: 1px solid {c["border"]};
                border-bottom: none;
                border-radius: {self._px(5)}px {self._px(5)}px 0 0;
                padding: {self._px(6)}px {self._px(18)}px;
                font-size: {self._pt_css(11)}pt;
                margin-right: {self._px(2)}px;
                min-width: {tab_min_width}px;
            }}
            QTabBar::tab:selected {{
                background-color: {c["tab_sel_bg"]};
                color: {c["tab_sel_text"]};
                font-weight: bold;
                border-bottom: {self._px(2)}px solid {c["tab_accent"]};
            }}
            QTabBar::tab:hover:!selected {{
                background-color: {c["tab_hover"]};
            }}
            QGroupBox {{
                font-weight: bold;
                font-size: {self._pt_css(11)}pt;
                border: 1px solid {c["border"]};
                border-radius: {self._px(8)}px;
                margin-top: {self._px(14)}px;
                padding-top: {self._px(6)}px;
                background-color: {c["bg_card"]};
                color: {c["text"]};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: {self._px(14)}px;
                padding: 0 {self._px(6)}px;
                color: {c["text_sub"]};
                background: transparent;
            }}
            QPushButton {{
                background-color: {c["btn_bg"]};
                border: 1px solid {c["btn_border"]};
                border-radius: {self._px(6)}px;
                padding: {self._px(6)}px {self._px(16)}px;
                color: {c["btn_text"]};
                font-size: {self._pt_css(10)}pt;
            }}
            QPushButton:hover {{
                background-color: {c["btn_hover_bg"]};
                border: 1px solid {c["btn_hover_border"]};
                color: {c["btn_hover_text"]};
            }}
            QPushButton:pressed {{
                background-color: {c["accent_dis"]};
                border: 1px solid {c["accent2"]};
            }}
            QPushButton:disabled {{
                background-color: {c["input_dis"]};
                color: {c["text_mute"]};
                border: 1px solid {c["border"]};
            }}
            QPushButton#start_button {{
                background-color: {c["accent"]};
                color: #ffffff;
                font-size: {self._pt_css(13)}pt;
                font-weight: bold;
                border: none;
                border-radius: {self._px(8)}px;
            }}
            QPushButton#start_button:hover {{
                background-color: {c["accent2"]};
            }}
            QPushButton#start_button:pressed {{
                background-color: {c["accent3"]};
            }}
            QPushButton#start_button:disabled {{
                background-color: {c["accent_dis"]};
                color: #ffffff;
            }}
            QPushButton#clear_button {{
                background-color: {c["clear_bg"]};
                color: {c["clear_text"]};
                font-size: {self._pt_css(10)}pt;
                font-weight: bold;
                border: 1px solid {c["clear_border"]};
                border-radius: {self._px(6)}px;
            }}
            QPushButton#clear_button:hover {{
                background-color: {c["clear_hover_bg"]};
                border: 1px solid {c["clear_hover_border"]};
                color: {c["clear_hover_text"]};
            }}
            QPushButton#theme_button {{
                background-color: transparent;
                border: 1px solid {c["border"]};
                border-radius: {self._px(6)}px;
                padding: {self._px(2)}px {self._px(8)}px;
                font-size: {self._pt_css(16)}pt;
                color: {c["text_sub"]};
            }}
            QPushButton#theme_button:hover {{
                background-color: {c["btn_hover_bg"]};
                border: 1px solid {c["btn_hover_border"]};
            }}
            QProgressBar {{
                border: none;
                border-radius: {rad_f}px;
                text-align: center;
                background-color: {c["progress_bg"]};
                color: {c["text"]};
                font-size: {self._pt_css(10)}pt;
            }}
            QProgressBar::chunk {{
                background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 {r}, stop:1 {r2});
                border-radius: {rad_f}px;
            }}
            QLineEdit {{
                border: 1px solid {c["btn_border"]};
                border-radius: {self._px(6)}px;
                padding: {self._px(4)}px {self._px(10)}px;
                background-color: {c["input_bg"]};
                color: {c["text"]};
                selection-background-color: {c["input_sel"]};
                font-size: {self._pt_css(10)}pt;
            }}
            QLineEdit:focus {{
                border: 1px solid {c["accent"]};
            }}
            QLineEdit:disabled {{
                background-color: {c["input_dis"]};
                color: {c["input_dis_text"]};
            }}
            QTextEdit {{
                border: 1px solid {c["border"]};
                border-radius: {self._px(6)}px;
                background-color: {c["input_bg"]};
                color: {c["text"]};
                selection-background-color: {c["input_sel"]};
                font-size: {self._pt_css(10)}pt;
            }}
            QCheckBox {{
                background: transparent;
                spacing: {self._px(8)}px;
                color: {c["text_sub"]};
                font-size: {self._pt_css(10)}pt;
            }}
            QCheckBox::indicator {{
                width: {self._px(18)}px;
                height: {self._px(18)}px;
                border: 1.5px solid {c["btn_border"]};
                border-radius: {self._px(4)}px;
                background-color: {c["input_bg"]};
            }}
            QCheckBox::indicator:checked {{
                background-color: {c["accent"]};
                border: 1.5px solid {c["accent"]};
            }}
            QCheckBox::indicator:hover {{
                border: 1.5px solid {c["accent"]};
            }}
            QCheckBox:disabled {{
                color: {c["text_mute"]};
            }}
            QCheckBox::indicator:disabled {{
                background-color: {c["input_dis"]};
                border: 1.5px solid {c["border"]};
            }}
            QLabel {{
                background: transparent;
                color: {c["text_sub"]};
                font-size: {self._pt_css(10)}pt;
            }}
            QScrollBar:vertical {{
                background: {c["scroll_bg"]};
                width: {self._px(8)}px;
                border-radius: {self._px(4)}px;
            }}
            QScrollBar::handle:vertical {{
                background: {c["scroll_handle"]};
                border-radius: {self._px(4)}px;
                min-height: {self._px(30)}px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {c["scroll_handle_hover"]};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
            QScrollBar:horizontal {{
                background: {c["scroll_bg"]};
                height: {self._px(8)}px;
                border-radius: {self._px(4)}px;
            }}
            QScrollBar::handle:horizontal {{
                background: {c["scroll_handle"]};
                border-radius: {self._px(4)}px;
                min-width: {self._px(30)}px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background: {c["scroll_handle_hover"]};
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
            #progress_card {{
                background-color: {c["bg_card"]};
                border: 1px solid {c["border"]};
                border-radius: {self._px(8)}px;
            }}
            #progress_card QLabel {{
                border: none;
                background: transparent;
            }}
            QListWidget#files_list {{
                background-color: {c["input_bg"]};
                border: 1px solid {c["border"]};
                border-radius: {self._px(6)}px;
                color: {c["text"]};
                font-size: {self._pt_css(10)}pt;
                padding: {self._px(2)}px;
            }}
            QListWidget#files_list::item {{
                padding: {self._px(3)}px {self._px(6)}px;
                border-radius: {self._px(4)}px;
            }}
            QListWidget#files_list::item:selected {{
                background-color: {c["accent"]};
                color: #ffffff;
            }}
            QListWidget#files_list::item:hover:!selected {{
                background-color: {c["btn_hover_bg"]};
            }}
            QSpinBox {{
                border: 1px solid {c["btn_border"]};
                border-radius: {self._px(6)}px;
                padding: {self._px(2)}px {self._px(8)}px;
                background-color: {c["input_bg"]};
                color: {c["text"]};
                selection-background-color: {c["input_sel"]};
                font-size: {self._pt_css(10)}pt;
            }}
            QSpinBox:focus {{
                border: 1px solid {c["accent"]};
            }}
            QSpinBox:disabled {{
                background-color: {c["input_dis"]};
                color: {c["input_dis_text"]};
            }}
            QPushButton#cancel_button {{
                background-color: {c["clear_bg"]};
                color: {c["clear_text"]};
                font-size: {self._pt_css(9)}pt;
                font-weight: bold;
                border: 1px solid {c["clear_border"]};
                border-radius: {self._px(6)}px;
                padding: {self._px(2)}px {self._px(12)}px;
            }}
            QPushButton#cancel_button:hover {{
                background-color: {c["clear_hover_bg"]};
                border: 1px solid {c["clear_hover_border"]};
                color: {c["clear_hover_text"]};
            }}
            QPushButton#open_result_button {{
                background-color: transparent;
                color: {c["accent"]};
                font-size: {self._pt_css(10)}pt;
                font-weight: bold;
                border: 1px solid {c["accent"]};
                border-radius: {self._px(6)}px;
            }}
            QPushButton#open_result_button:hover {{
                background-color: {c["btn_hover_bg"]};
                border: 1px solid {c["btn_hover_border"]};
            }}
            QMenuBar {{
                background-color: {c["bg_card"]};
                color: {c["text_sub"]};
                border-bottom: 1px solid {c["border"]};
                font-size: {self._pt_css(10)}pt;
            }}
            QMenuBar::item {{
                background: transparent;
                padding: {self._px(4)}px {self._px(10)}px;
            }}
            QMenuBar::item:selected {{
                background-color: {c["btn_hover_bg"]};
                color: {c["btn_hover_text"]};
                border-radius: {self._px(4)}px;
            }}
            QMenu {{
                background-color: {c["bg_card"]};
                color: {c["text_sub"]};
                border: 1px solid {c["border"]};
                padding: {self._px(4)}px;
            }}
            QMenu::item {{
                padding: {self._px(5)}px {self._px(22)}px;
                border-radius: {self._px(4)}px;
            }}
            QMenu::item:selected {{
                background-color: {c["accent"]};
                color: #ffffff;
            }}
            QMenu::separator {{
                height: 1px;
                background: {c["border"]};
                margin: {self._px(4)}px {self._px(6)}px;
            }}
            QStatusBar {{
                background-color: {c["status_bg"]};
                color: {c["text_mute2"]};
                font-size: {self._pt_css(9)}pt;
            }}
            QToolTip {{
                background-color: {c["bg_card"]};
                color: {c["text"]};
                border: 1px solid {c["accent"]};
                padding: {self._px(4)}px;
            }}
        """)
        self._style_drop_hint()

        # Обновляем динамические стили прогресс-баров файла (тонкий)
        if hasattr(self, 'progress_bar_file'):
            self.progress_bar_file.setStyleSheet(
                f"QProgressBar {{ border: none; border-radius: {rad_s}px;"
                f"  background-color: {c['progress_bg']}; text-align: center;"
                f"  color: {c['text']}; font-size: {self._pt_css(8)}pt; font-weight: 600; }}"
                f"QProgressBar::chunk {{ border-radius: {rad_s}px;"
                f"  background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                f"  stop:0 {r}, stop:1 {r2}); }}"
            )
        if hasattr(self, 'progress_bar_total'):
            self.progress_bar_total.setStyleSheet(
                f"QProgressBar {{ border: none; border-radius: {rad_f}px;"
                f"  background-color: {c['progress_bg']}; text-align: center;"
                f"  color: {c['text']}; font-size: {self._pt_css(10)}pt; font-weight: 600; }}"
                f"QProgressBar::chunk {{ border-radius: {rad_f}px;"
                f"  background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                f"  stop:0 {r}, stop:1 {r2}); }}"
            )
        if hasattr(self, 'progress_upload'):
            self.progress_upload.setStyleSheet(
                f"QProgressBar {{ border: none; background-color: {c['progress_bg']};"
                f"  border-radius: {self._px(3)}px; }}"
                f"QProgressBar::chunk {{ background-color: {c['accent']}; border-radius: {self._px(3)}px; }}"
            )
        if hasattr(self, 'lbl_file_counter'):
            self.lbl_file_counter.setStyleSheet(
                f"color: {c['accent']}; font-size: {self._pt_css(11)}pt; font-weight: bold;"
            )
        if hasattr(self, 'lbl_status'):
            self.lbl_status.setStyleSheet(
                f"color: {c['status_text']}; font-size: {self._pt_css(10)}pt; font-weight: bold;"
                f"background-color: {c['status_bg']}; border-radius: {self._px(6)}px; padding: {self._px(2)}px;"
            )
        if hasattr(self, 'lbl_stage'):
            self.lbl_stage.setStyleSheet(
                self._transparent_label_style(c["text_sub"], font_pt=9, font_weight="600")
            )
        if hasattr(self, 'lbl_current_file'):
            self.lbl_current_file.setStyleSheet(self._transparent_label_style(c["text_mute2"], font_pt=9))

    # ──────────────────────────────────────────────────────────────
    # UI
    # ──────────────────────────────────────────────────────────────

    def _init_ui(self):
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(self._px(940), self._px(600))
        self.resize(self._px(1040), self._px(940))

        self._build_menu_bar()

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(self._px(16), self._px(12), self._px(16), self._px(12))
        root_layout.setSpacing(self._px(8))
        self.setCentralWidget(root)

        # Заголовок + кнопка темы
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)

        title_label = QLabel("GigaAM v3: Транскрибация")
        title_label.setFont(self._font(18, QFont.Weight.Bold))
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setFixedHeight(self._px(40))
        header_row.addWidget(title_label, 1)

        self._btn_theme = QPushButton(self._colors()["theme_btn"])
        self._btn_theme.setObjectName("theme_button")
        self._btn_theme.setFixedSize(self._px(42), self._px(36))
        self._btn_theme.setToolTip("Переключить тему")
        self._btn_theme.clicked.connect(self._toggle_theme)
        header_row.addWidget(self._btn_theme)

        root_layout.addLayout(header_row)

        tabs = QTabWidget()
        tabs.tabBar().setElideMode(Qt.TextElideMode.ElideNone)
        root_layout.addWidget(tabs, 1)

        # ── Вкладка «Обработка» ──
        content_widget = QWidget()
        main_layout = QVBoxLayout(content_widget)
        main_layout.setContentsMargins(self._px(8), self._px(6), self._px(8), self._px(6))
        main_layout.setSpacing(self._px(4))

        main_layout.addWidget(self._create_files_group())
        main_layout.addWidget(self._create_output_group())
        main_layout.addWidget(self._create_diarization_group())
        main_layout.addWidget(self._create_formats_group())

        self.btn_start = QPushButton("ЗАПУСТИТЬ ОБРАБОТКУ")
        self.btn_start.setObjectName("start_button")
        self.btn_start.setFixedHeight(self._px(52))
        self.btn_start.setToolTip("Начать транскрибацию выбранных файлов  (Ctrl+Enter)")
        self.btn_start.setShortcut(QKeySequence("Ctrl+Return"))
        self.btn_start.clicked.connect(self._start_processing_thread)
        main_layout.addWidget(self.btn_start)

        self._create_progress_section(main_layout)

        self.btn_clear = QPushButton("ОЧИСТИТЬ ВСЕ")
        self.btn_clear.setObjectName("clear_button")
        self.btn_clear.setFixedHeight(self._px(40))
        self.btn_clear.setToolTip("Сбросить файлы, папки, журнал и прогресс")
        self.btn_clear.clicked.connect(self._clear_all)
        main_layout.addWidget(self.btn_clear)

        main_layout.addStretch()

        proc_scroll = QScrollArea()
        proc_scroll.setWidgetResizable(True)
        proc_scroll.setFrameShape(QFrame.Shape.NoFrame)
        proc_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        proc_scroll.setWidget(content_widget)
        tabs.addTab(proc_scroll, "Обработка")

        # ── Вкладка «Журнал» ──
        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        log_layout.setContentsMargins(self._px(8), self._px(8), self._px(8), self._px(8))
        log_layout.setSpacing(self._px(6))

        log_toolbar = QHBoxLayout()
        log_toolbar.setSpacing(self._px(8))
        btn_log_copy = QPushButton("Копировать")
        btn_log_copy.setToolTip("Скопировать весь журнал в буфер обмена")
        btn_log_copy.setFixedHeight(self._px(32))
        btn_log_copy.clicked.connect(self._copy_log)
        log_toolbar.addWidget(btn_log_copy)
        btn_log_save = QPushButton("Сохранить…")
        btn_log_save.setToolTip("Сохранить журнал в текстовый файл")
        btn_log_save.setFixedHeight(self._px(32))
        btn_log_save.clicked.connect(self._save_log)
        log_toolbar.addWidget(btn_log_save)
        btn_log_clear = QPushButton("Очистить журнал")
        btn_log_clear.setToolTip("Очистить только журнал, не сбрасывая настройки")
        btn_log_clear.setFixedHeight(self._px(32))
        btn_log_clear.clicked.connect(self._clear_log)
        log_toolbar.addWidget(btn_log_clear)
        log_toolbar.addStretch()
        log_layout.addLayout(log_toolbar)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(self._font(11, fixed=True))
        self.log_text.setMinimumHeight(self._px(160))
        log_layout.addWidget(self.log_text, 1)
        tabs.addTab(log_tab, "Журнал обработки")
        self.tabs = tabs

        # Статус-бар: краткие подсказки и состояние
        self.status_bar = self.statusBar()
        self.status_bar.showMessage("Готов к работе")

        # Esc — отмена текущей обработки
        esc = QAction(self)
        esc.setShortcut(QKeySequence(Qt.Key.Key_Escape))
        esc.triggered.connect(self._cancel_processing)
        self.addAction(esc)

        self._apply_theme()
        self._restore_geometry()

    # ──────────────────────────────────────────────────────────────
    # Меню, статус, геометрия окна, журнал
    # ──────────────────────────────────────────────────────────────

    def _build_menu_bar(self):
        menubar = self.menuBar()
        menubar.clear()

        file_menu = menubar.addMenu("Файл")
        act_files = QAction("Выбрать файлы…", self)
        act_files.setShortcut(QKeySequence.StandardKey.Open)
        act_files.setStatusTip("Добавить аудио- или видеофайлы в очередь")
        act_files.triggered.connect(self._select_files)
        file_menu.addAction(act_files)

        act_folder = QAction("Выбрать папку с файлами…", self)
        act_folder.setStatusTip("Добавить все медиафайлы из папки и подпапок")
        act_folder.triggered.connect(self._select_files_folder)
        file_menu.addAction(act_folder)

        act_out = QAction("Папка сохранения…", self)
        act_out.setStatusTip("Выбрать папку для результатов транскрибации")
        act_out.triggered.connect(self._select_output_folder)
        file_menu.addAction(act_out)

        file_menu.addSeparator()
        act_open_res = QAction("Открыть папку с результатами", self)
        act_open_res.setStatusTip("Открыть папку с готовыми файлами")
        act_open_res.triggered.connect(self._open_results_folder)
        file_menu.addAction(act_open_res)

        file_menu.addSeparator()
        act_quit = QAction("Выход", self)
        act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        view_menu = menubar.addMenu("Вид")
        self._act_theme = QAction("Переключить тему", self)
        self._act_theme.setShortcut(QKeySequence("Ctrl+T"))
        self._act_theme.setStatusTip("Светлая / тёмная тема оформления")
        self._act_theme.triggered.connect(self._toggle_theme)
        view_menu.addAction(self._act_theme)

        settings_menu = menubar.addMenu("Настройки")
        act_device = QAction("Устройство (CPU / GPU)…", self)
        act_device.setStatusTip("Выбрать CPU или видеокарту NVIDIA для распознавания")
        act_device.triggered.connect(self._change_device)
        settings_menu.addAction(act_device)

        help_menu = menubar.addMenu("Справка")
        act_about = QAction("О программе", self)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

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
            self.status_bar.showMessage(message)

    def _copy_log(self):
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.log_text.toPlainText())
            self._set_status("Журнал скопирован в буфер обмена")

    def _save_log(self):
        if not self.log_text.toPlainText().strip():
            QMessageBox.information(self, "Журнал пуст", "Журнал пока пуст — нечего сохранять.")
            return
        initial_dir = self.user_settings.get_last_output_dir() or self.output_dir or os.path.expanduser("~")
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить журнал",
            os.path.join(initial_dir, "transcription_log.txt"),
            "Текстовые файлы (*.txt);;Все файлы (*.*)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.log_text.toPlainText())
            self._set_status(f"Журнал сохранён: {os.path.basename(path)}")
            self.log(f"Журнал сохранён в {path}")
        except OSError as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось сохранить журнал:\n{e}")

    def _clear_log(self):
        self.log_text.clear()
        self._set_status("Журнал очищен")

    def _open_results_folder(self):
        target = self._last_result_dir or self.output_dir or self.input_dir
        if not target or not os.path.isdir(target):
            QMessageBox.information(
                self, "Папка недоступна",
                "Папка с результатами ещё не определена.\n"
                "Запустите обработку или выберите папку сохранения."
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    def _show_about(self):
        QMessageBox.about(
            self, "О программе",
            f"<b>{APP_TITLE}</b><br><br>"
            "Локальная транскрибация аудио и видео на модели "
            "<b>GigaAM v3</b> с поддержкой диаризации спикеров.<br><br>"
            "Возможности: пакетная обработка, загрузка по ссылке, "
            "таймкоды, экспорт в TXT / Markdown / SRT / VTT.<br><br>"
            "Поддерживаемые форматы ввода: mp3, wav, m4a, aac, flac, ogg, "
            "mp4, avi, mov, mkv, webm, wma, 3gp."
        )

    _ACCENT_LIGHT = "#3b82f6"
    _CONVERSION_BAND = 0.15

    def _make_progress_bar(self, height: int, font_pt: int) -> QProgressBar:
        c = self._colors()
        bar = QProgressBar()
        scaled_height = self._px(height)
        bar.setFixedHeight(scaled_height)
        bar.setTextVisible(True)
        bar.setRange(0, 100)
        radius = scaled_height // 2
        r, r2 = c["progress_chunk"], c["progress_chunk2"]
        bar.setStyleSheet(
            f"QProgressBar {{ border: none; border-radius: {radius}px;"
            f"  background-color: {c['progress_bg']}; text-align: center; color: {c['text']};"
            f"  font-size: {self._pt_css(font_pt)}pt; font-weight: 600; }}"
            f"QProgressBar::chunk {{ border-radius: {radius}px;"
            f"  background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"  stop:0 {r}, stop:1 {r2}); }}"
        )
        return bar

    def _create_progress_section(self, parent_layout):
        c = self._colors()
        progress_frame = QFrame()
        progress_frame.setObjectName("progress_card")
        frame_layout = QVBoxLayout(progress_frame)
        frame_layout.setContentsMargins(self._px(16), self._px(12), self._px(16), self._px(12))
        frame_layout.setSpacing(self._px(6))

        head_row = QHBoxLayout()
        lbl_overall = QLabel("Общий прогресс")
        lbl_overall.setStyleSheet(self._transparent_label_style(c["text_sub"], font_pt=11, font_weight="bold"))
        head_row.addWidget(lbl_overall)
        head_row.addStretch()
        self.lbl_file_counter = QLabel("")
        self.lbl_file_counter.setStyleSheet(self._transparent_label_style(c["accent"], font_pt=11, font_weight="bold"))
        head_row.addWidget(self.lbl_file_counter)
        self.btn_cancel = QPushButton("Отменить")
        self.btn_cancel.setObjectName("cancel_button")
        self.btn_cancel.setToolTip("Остановить обработку после текущего файла  (Esc)")
        self.btn_cancel.setFixedHeight(self._px(28))
        self.btn_cancel.clicked.connect(self._cancel_processing)
        self.btn_cancel.setVisible(False)
        head_row.addWidget(self.btn_cancel)
        frame_layout.addLayout(head_row)

        self.progress_bar_total = self._make_progress_bar(height=22, font_pt=10)
        frame_layout.addWidget(self.progress_bar_total)

        self.detail_row = QWidget()
        detail_layout = QHBoxLayout(self.detail_row)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(self._px(10))
        self.lbl_stage = QLabel("")
        self.lbl_stage.setStyleSheet(self._transparent_label_style(c["text_sub"], font_pt=9, font_weight="600"))
        detail_layout.addWidget(self.lbl_stage)
        detail_layout.addStretch()
        self.lbl_current_file = QLabel("")
        self.lbl_current_file.setStyleSheet(self._transparent_label_style(c["text_mute2"], font_pt=9))
        detail_layout.addWidget(self.lbl_current_file)
        frame_layout.addWidget(self.detail_row)
        self.detail_row.setVisible(False)

        self.progress_bar_file = self._make_progress_bar(height=16, font_pt=8)
        frame_layout.addWidget(self.progress_bar_file)

        self.lbl_status = QLabel("Готов к работе")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setFixedHeight(self._px(28))
        self.lbl_status.setStyleSheet(
            f"color: {c['status_text']}; font-size: {self._pt_css(10)}pt; font-weight: bold;"
            f"background-color: {c['status_bg']}; border-radius: {self._px(6)}px; padding: {self._px(2)}px;"
        )
        frame_layout.addWidget(self.lbl_status)

        self.btn_open_result = QPushButton("Открыть папку с результатами")
        self.btn_open_result.setObjectName("open_result_button")
        self.btn_open_result.setToolTip("Открыть папку с готовыми файлами в проводнике")
        self.btn_open_result.setFixedHeight(self._px(34))
        self.btn_open_result.clicked.connect(self._open_results_folder)
        self.btn_open_result.setVisible(False)
        frame_layout.addWidget(self.btn_open_result)

        parent_layout.addWidget(progress_frame)

    def _create_files_group(self) -> QGroupBox:
        group = QGroupBox("1. Выбор файлов")
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(self._px(12), self._px(18), self._px(12), self._px(8))
        main_layout.setSpacing(self._px(6))

        row1 = QHBoxLayout()
        row1.setSpacing(self._px(10))
        btn_select_files = QPushButton("Выбрать файлы")
        btn_select_files.setToolTip("Выбрать аудио/видео файлы для обработки  (Ctrl+O)")
        btn_select_files.clicked.connect(self._select_files)
        btn_select_files.setFixedHeight(self._px(36))
        btn_select_files.setMinimumWidth(self._px(160))
        row1.addWidget(btn_select_files)

        btn_select_folder = QPushButton("Выбрать папку")
        btn_select_folder.setToolTip("Добавить все медиафайлы из папки и подпапок")
        btn_select_folder.clicked.connect(self._select_files_folder)
        btn_select_folder.setFixedHeight(self._px(36))
        btn_select_folder.setMinimumWidth(self._px(150))
        row1.addWidget(btn_select_folder)

        self.input_path = QLineEdit()
        self.input_path.setPlaceholderText("Ссылка на медиа (YouTube и др.)")
        self.input_path.setToolTip("Вставьте ссылку и нажмите «Загрузить»")
        self.input_path.setFixedHeight(self._px(36))
        self.input_path.setMinimumWidth(self._px(200))
        self.input_path.returnPressed.connect(self._start_download)
        row1.addWidget(self.input_path, 1)

        self.btn_upload = QPushButton("Загрузить")
        self.btn_upload.setToolTip("Скачать медиа по ссылке и добавить в очередь")
        self.btn_upload.setFixedHeight(self._px(36))
        self.btn_upload.setMinimumWidth(self._px(100))
        self.btn_upload.clicked.connect(self._start_download)
        row1.addWidget(self.btn_upload)

        self.progress_upload = QProgressBar()
        self.progress_upload.setFixedHeight(self._px(36))
        self.progress_upload.setFixedWidth(self._px(90))
        self.progress_upload.setValue(0)
        self.progress_upload.setTextVisible(True)
        self.progress_upload.setVisible(False)
        row1.addWidget(self.progress_upload)
        main_layout.addLayout(row1)

        # Подсказка о выбранной папке источника
        self.lbl_input_folder = QLabel("Папка не выбрана")
        self.lbl_input_folder.setStyleSheet(self._transparent_label_style(self._colors()["text_mute"], font_pt=9))
        main_layout.addWidget(self.lbl_input_folder)

        # Очередь файлов + пустое состояние (drop-зона)
        self.drop_hint = QLabel("Перетащите сюда файлы или папки  ·  либо нажмите «Выбрать файлы»")
        self.drop_hint.setObjectName("drop_hint")
        self.drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_hint.setMinimumHeight(self._px(50))
        main_layout.addWidget(self.drop_hint)

        self.files_list = QListWidget()
        self.files_list.setObjectName("files_list")
        self.files_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.files_list.setMinimumHeight(self._px(72))
        self.files_list.setMaximumHeight(self._px(150))
        self.files_list.setToolTip("Очередь файлов. Выделите и нажмите Delete, чтобы убрать.")
        self.files_list.itemSelectionChanged.connect(self._update_files_controls)
        self.files_list.setVisible(False)
        main_layout.addWidget(self.files_list)

        controls = QHBoxLayout()
        controls.setSpacing(self._px(10))
        self.lbl_files_count = QLabel("Файлы не выбраны")
        self.lbl_files_count.setStyleSheet(self._transparent_label_style(self._colors()["text_mute"]))
        controls.addWidget(self.lbl_files_count)
        controls.addStretch()
        self.btn_remove_file = QPushButton("Убрать выбранное")
        self.btn_remove_file.setToolTip("Убрать выделенные файлы из очереди  (Delete)")
        self.btn_remove_file.setFixedHeight(self._px(32))
        self.btn_remove_file.setEnabled(False)
        self.btn_remove_file.clicked.connect(self._remove_selected_files)
        controls.addWidget(self.btn_remove_file)
        self.btn_clear_files = QPushButton("Очистить список")
        self.btn_clear_files.setToolTip("Убрать все файлы из очереди (настройки сохранятся)")
        self.btn_clear_files.setFixedHeight(self._px(32))
        self.btn_clear_files.setEnabled(False)
        self.btn_clear_files.clicked.connect(self._clear_files_list)
        controls.addWidget(self.btn_clear_files)
        main_layout.addLayout(controls)

        group.setLayout(main_layout)
        return group

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
        c = self._colors()
        if has_files:
            self.lbl_files_count.setText(f"Выбрано файлов: {len(self.files_to_process)}")
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
        super().keyPressEvent(event)

    def _style_drop_hint(self):
        if not hasattr(self, "drop_hint"):
            return
        c = self._colors()
        active = getattr(self, "_drop_active", False)
        border = c["accent"] if active else c["border"]
        bg = c["btn_hover_bg"] if active else c["bg_card"]
        text = c["accent"] if active else c["text_mute2"]
        self.drop_hint.setStyleSheet(
            f"#drop_hint {{ border: 2px dashed {border}; border-radius: {self._px(10)}px;"
            f"  background-color: {bg}; color: {text};"
            f"  font-size: {self._pt_css(11)}pt; padding: {self._px(8)}px; }}"
        )

    def _create_output_group(self) -> QGroupBox:
        group = QGroupBox("2. Папка сохранения результатов")
        layout = QHBoxLayout()
        layout.setContentsMargins(self._px(12), self._px(20), self._px(12), self._px(10))
        layout.setSpacing(self._px(12))
        btn_output = QPushButton("Выбрать папку")
        btn_output.clicked.connect(self._select_output_folder)
        btn_output.setMinimumWidth(self._px(220))
        btn_output.setFixedHeight(self._px(36))
        layout.addWidget(btn_output)
        self.lbl_output_folder = QLabel("Папка не выбрана (по умолчанию - рядом с файлом)")
        self.lbl_output_folder.setStyleSheet(self._transparent_label_style(self._colors()["text_mute"]))
        layout.addWidget(self.lbl_output_folder, 1)
        group.setLayout(layout)
        return group

    def _create_diarization_group(self) -> QGroupBox:
        group = QGroupBox("3. Диаризация спикеров")
        layout = QVBoxLayout()
        layout.setContentsMargins(self._px(12), self._px(20), self._px(12), self._px(10))
        layout.setSpacing(self._px(8))
        self.cb_diarization = QCheckBox("Включить диаризацию спикеров")
        self.cb_diarization.setToolTip("Определять, кто из спикеров говорит (нужен HF_TOKEN)")
        self.cb_diarization.stateChanged.connect(self._toggle_diarization)
        layout.addWidget(self.cb_diarization)
        speakers_layout = QHBoxLayout()
        speakers_layout.setSpacing(self._px(12))
        speakers_layout.addWidget(QLabel("Кол-во спикеров:"))
        self.entry_num_speakers = QSpinBox()
        self.entry_num_speakers.setRange(0, 20)
        self.entry_num_speakers.setValue(0)
        self.entry_num_speakers.setSpecialValueText("Авто")
        self.entry_num_speakers.setToolTip("0 = автоопределение количества спикеров")
        self.entry_num_speakers.setEnabled(False)
        self.entry_num_speakers.setFixedHeight(self._px(32))
        self.entry_num_speakers.setMinimumWidth(self._px(140))
        self.entry_num_speakers.setMaximumWidth(self._px(200))
        speakers_layout.addWidget(self.entry_num_speakers)
        speakers_layout.addStretch()
        layout.addLayout(speakers_layout)
        info_label = QLabel("Автоматическое определение спикеров (требуется HF_TOKEN)")
        info_label.setStyleSheet(self._transparent_label_style(self._colors()["text_mute2"], font_pt=9))
        layout.addWidget(info_label)
        group.setLayout(layout)
        return group

    def _create_formats_group(self) -> QGroupBox:
        group = QGroupBox("4. Форматы вывода")
        layout = QVBoxLayout()
        layout.setContentsMargins(self._px(12), self._px(20), self._px(12), self._px(10))
        layout.setSpacing(self._px(6))
        self.format_checkboxes = {}

        row1 = QHBoxLayout()
        row1.setSpacing(self._px(20))
        for fmt in ['txt', 'txt_timecodes', 'txt_diarize', 'txt_diarize_timecodes']:
            cb = QCheckBox(OUTPUT_FORMATS[fmt])
            cb.setChecked(fmt in ('txt', 'txt_timecodes'))
            if fmt in ('txt_diarize', 'txt_diarize_timecodes'):
                cb.setEnabled(False)
            cb.stateChanged.connect(lambda state, f=fmt: self._toggle_format(f))
            row1.addWidget(cb)
            self.format_checkboxes[fmt] = cb
        row1.addStretch()
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(self._px(20))
        for fmt in ('md', 'srt', 'vtt'):
            cb = QCheckBox(OUTPUT_FORMATS[fmt])
            cb.setChecked(False)
            cb.stateChanged.connect(lambda state, f=fmt: self._toggle_format(f))
            row2.addWidget(cb)
            self.format_checkboxes[fmt] = cb
        row2.addStretch()
        layout.addLayout(row2)

        group.setLayout(layout)
        return group

    # ──────────────────────────────────────────────────────────────
    # Диалог HF токена
    # ──────────────────────────────────────────────────────────────

    def _show_hf_token_dialog(self) -> bool:
        dlg = QDialog(self)
        dlg.setWindowTitle("HuggingFace токен для диаризации")
        dlg.setMinimumWidth(self._px(520))
        layout = QVBoxLayout(dlg)
        layout.setSpacing(self._px(10))
        info = QLabel(
            "<b>Диаризация спикеров требует HuggingFace токен</b><br><br>"
            "1. Создайте аккаунт на <a href='https://huggingface.co'>huggingface.co</a><br>"
            "2. Получите токен: <a href='https://huggingface.co/settings/tokens'>huggingface.co/settings/tokens</a><br>"
            "3. Примите условия доступа к моделям:<br>"
            "&nbsp;&nbsp;&nbsp;<a href='https://huggingface.co/pyannote/speaker-diarization-3.1'>pyannote/speaker-diarization-3.1</a><br>"
            "&nbsp;&nbsp;&nbsp;<a href='https://huggingface.co/pyannote/segmentation-3.0'>pyannote/segmentation-3.0</a><br><br>"
            "Вставьте ваш токен ниже (начинается с <b>hf_</b>):"
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
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        token = token_input.text().strip()
        if not token.startswith("hf_"):
            QMessageBox.warning(self, "Неверный токен", "Токен должен начинаться с 'hf_'")
            return False
        os.environ["HF_TOKEN"] = token
        env_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            '.env'
        )
        try:
            lines = []
            if os.path.exists(env_path):
                with open(env_path) as f:
                    lines = [line for line in f.readlines() if not line.startswith('HF_TOKEN=')]
            lines.append(f'HF_TOKEN={token}\n')
            with open(env_path, 'w') as f:
                f.writelines(lines)
            self.log("Токен сохранён в .env")
        except Exception:
            pass
        return True

    def _toggle_diarization(self, state):
        enabling = (state == Qt.CheckState.Checked.value)
        if enabling and not os.getenv("HF_TOKEN", "").startswith("hf_"):
            if not self._show_hf_token_dialog():
                self.cb_diarization.blockSignals(True)
                self.cb_diarization.setChecked(False)
                self.cb_diarization.blockSignals(False)
                return
        self.enable_diarization = enabling
        self.entry_num_speakers.setEnabled(self.enable_diarization)
        for fmt in ('txt_diarize', 'txt_diarize_timecodes'):
            cb = self.format_checkboxes.get(fmt)
            if cb:
                cb.setEnabled(self.enable_diarization)
        if self.enable_diarization:
            self.log("Диаризация спикеров: ВКЛЮЧЕНА")
        else:
            self.entry_num_speakers.setValue(0)
            self.log("Диаризация спикеров: ВЫКЛЮЧЕНА")

    def _toggle_format(self, fmt: str):
        self.output_formats[fmt] = self.format_checkboxes[fmt].isChecked()
        if not any(self.output_formats.values()):
            self.output_formats['txt'] = True
            self.format_checkboxes['txt'].setChecked(True)
            self.log("ПРЕДУПРЕЖДЕНИЕ: Выбран хотя бы один формат по умолчанию (txt)")

    def _get_selected_formats(self) -> list:
        return [fmt for fmt, enabled in self.output_formats.items() if enabled]

    # ──────────────────────────────────────────────────────────────
    # Файлы / папки
    # ──────────────────────────────────────────────────────────────

    def log(self, message: str):
        self.signals.log_message.emit(message)
        self.app_logger.get_logger().info(message)

    def _append_log(self, message: str):
        self.log_text.append(f">> {message}")

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
        files = []
        for url in urls:
            path = url.toLocalFile()
            if not path:
                continue
            if os.path.isdir(path):
                for root, _dirs, filenames in os.walk(path):
                    for f in filenames:
                        if f.lower().endswith(MEDIA_EXTENSIONS):
                            files.append(os.path.join(root, f))
            elif os.path.isfile(path) and path.lower().endswith(MEDIA_EXTENSIONS):
                files.append(path)
        if files:
            self._apply_dropped_or_selected_files(files)
        elif urls:
            QMessageBox.information(self, "Неподдерживаемый формат",
                "Сброшенные файлы не являются поддерживаемыми медиа "
                "(mp3, wav, m4a, aac, flac, ogg, mp4, avi, mov, mkv, webm, wma, qta, 3gp).")
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
                self.log(f"Добавлено из папки (включая подпапки): {len(files)} файлов")
                for f in files:
                    self.log(f" + {os.path.relpath(f, folder)}")
            else:
                QMessageBox.information(self, "Информация", "В выбранной папке и подпапках нет поддерживаемых файлов")

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
        self.lbl_input_folder.setText(f"Папка источника: {display_path}")
        self.lbl_input_folder.setStyleSheet(self._transparent_label_style(self._colors()["text_sub"], font_pt=9))

    def _update_output_dir_label(self, path: str):
        display_path = path if len(path) < 60 else f"...{path[-60:]}"
        self.lbl_output_folder.setText(display_path)
        self.lbl_output_folder.setStyleSheet(self._transparent_label_style(self._colors()["text_sub"]))

    # ──────────────────────────────────────────────────────────────
    # Загрузка по ссылке
    # ──────────────────────────────────────────────────────────────

    def _start_download(self, start_after_download: bool = False):
        url = self.input_path.text().strip()
        if not url:
            QMessageBox.warning(self, "Внимание", "Введите ссылку для загрузки.")
            return
        if not (url.startswith("http://") or url.startswith("https://")):
            QMessageBox.warning(self, "Внимание", "Ссылка должна начинаться с http:// или https://.")
            return
        if self.is_processing:
            QMessageBox.warning(self, "Внимание", "Дождитесь завершения обработки файлов.")
            return
        if self.is_downloading:
            QMessageBox.information(self, "Информация", "Загрузка уже выполняется.")
            return
        download_dir = self.input_dir
        if not download_dir:
            initial_dir = self.user_settings.get_last_files_dir() or os.path.expanduser("~")
            download_dir = QFileDialog.getExistingDirectory(self, "Выберите папку для загрузки медиа", initial_dir)
            if not download_dir:
                return
            self.input_dir = download_dir
            self.user_settings.set_last_files_dir(download_dir)
            self._update_input_dir_label(download_dir)
        self.is_downloading = True
        self.start_processing_after_download = start_after_download
        self.btn_upload.setEnabled(False)
        self.btn_start.setEnabled(False)
        self.progress_upload.setValue(0)
        self.progress_upload.setVisible(True)
        self.lbl_status.setText("Загрузка медиа по ссылке...")
        self._set_status("Загрузка по ссылке…")
        self.log(f"Загрузка медиа по ссылке в папку: {download_dir}")
        threading.Thread(target=self._download_media, args=(url, download_dir), daemon=True).start()

    def _download_media(self, url: str, download_dir: str):
        try:
            result = self.media_downloader.download(
                url, download_dir,
                progress_callback=self.signals.download_progress.emit,
                allow_playlist=False,
            )
            files = [p for p in result.files if os.path.isfile(p) and os.path.getsize(p) > 0]
            if not files:
                raise RuntimeError("yt-dlp не вернул скачанный медиафайл")
            self.signals.download_finished.emit(files)
        except Exception as e:
            self.signals.download_failed.emit(str(e))

    def _update_download_progress(self, value: int):
        self.progress_upload.setValue(value)

    def _on_download_finished(self, files: list):
        self.is_downloading = False
        self.btn_upload.setEnabled(True)
        self.btn_start.setEnabled(True)
        self.progress_upload.setValue(0)
        self.progress_upload.setVisible(False)
        if files:
            self._apply_dropped_or_selected_files(files, append=True, remember_dir=False)
            self.input_path.clear()
            self.lbl_status.setText("Медиа загружено и добавлено в очередь")
            self.log(f"Загрузка завершена: {len(files)} файлов")
        else:
            QMessageBox.warning(self, "Загрузка", "Не удалось получить медиафайлы по ссылке.")
            self.log("Загрузка завершилась без файлов")
        if self.start_processing_after_download:
            self.start_processing_after_download = False
            QTimer.singleShot(0, self._start_processing_thread)

    def _on_download_failed(self, message: str):
        self.is_downloading = False
        self.start_processing_after_download = False
        self.btn_upload.setEnabled(True)
        self.btn_start.setEnabled(True)
        self.progress_upload.setValue(0)
        self.progress_upload.setVisible(False)
        self.lbl_status.setText("Ошибка загрузки")
        self.log(f"Ошибка загрузки: {message}")
        QMessageBox.warning(self, "Ошибка загрузки", message)

    # ──────────────────────────────────────────────────────────────
    # Обработка файлов
    # ──────────────────────────────────────────────────────────────

    def _clear_all(self):
        if self.is_processing:
            reply = QMessageBox.question(
                self, "Внимание",
                "Идет обработка файлов. Вы уверены, что хотите сбросить все настройки?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.No:
                return
            self._cancel_requested = True
            self.is_processing = False
            self.progress_timer.stop()
            self._set_processing_controls_enabled(True)
        self.files_to_process = []
        self.output_dir = ""
        self.input_dir = ""
        self.files_processed = 0
        self.total_files = 0
        self.time_spent = 0
        self.current_file_start_time = 0
        self.start_time = None
        self.file_estimates = {}
        self.total_estimated_time = 0
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
        self.lbl_status.setText("Готов к работе")
        self.btn_cancel.setVisible(False)
        self.btn_cancel.setEnabled(True)
        self.btn_cancel.setText("Отменить")
        self.btn_open_result.setVisible(False)
        c = self._colors()
        self._refresh_files_list()
        self.lbl_input_folder.setText("Папка не выбрана")
        self.lbl_input_folder.setStyleSheet(self._transparent_label_style(c["text_mute"], font_pt=9))
        self.lbl_output_folder.setText("Папка не выбрана (по умолчанию - рядом с файлом)")
        self.lbl_output_folder.setStyleSheet(self._transparent_label_style(c["text_mute"]))
        self.input_path.clear()
        self.btn_start.setEnabled(True)
        self.btn_upload.setEnabled(True)
        self._set_status("Готов к работе")
        self.log("Все настройки сброшены")

    def _cancel_processing(self):
        if not self.is_processing or self._cancel_requested:
            return
        self._cancel_requested = True
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setText("Отмена…")
        self.lbl_stage.setText("●  Останавливаем после текущего файла…")
        self.lbl_status.setText("Отмена: дождитесь завершения текущего файла…")
        self._set_status("Отмена обработки…")
        self.log("Запрошена отмена обработки — остановимся после текущего файла")

    def _start_processing_thread(self):
        if self.is_processing:
            return
        if self.is_downloading:
            QMessageBox.information(self, "Информация", "Дождитесь завершения загрузки по ссылке.")
            return
        if self.input_path.text().strip():
            self._start_download(start_after_download=True)
            return
        if not self.files_to_process:
            QMessageBox.warning(self, "Внимание", "Выберите хотя бы один файл для обработки!")
            return
        if not self.output_dir:
            self.log("Папка сохранения не выбрана. Результаты будут сохраняться рядом с каждым исходным файлом.")
        self.is_processing = True
        self._cancel_requested = False
        self.start_time = time.time()
        self.files_processed = 0
        self.total_files = len(self.files_to_process)
        self.time_spent = 0
        self.current_file_start_time = 0
        self.current_stage = None
        self.current_stage_progress = 0.0
        self.log("Анализ файлов и оценка времени обработки...")
        files_with_durations = []
        for filepath in self.files_to_process:
            try:
                duration = AudioConverter.get_media_duration(filepath)
                if duration > 0:
                    self.log(f"  {os.path.basename(filepath)}: {int(duration//60)}:{int(duration%60):02d}")
                    files_with_durations.append((filepath, duration))
                else:
                    self.log(f"  {os.path.basename(filepath)}: длительность неизвестна")
                    files_with_durations.append((filepath, 60))
            except Exception:
                self.log(f"  {os.path.basename(filepath)}: ошибка определения длительности")
                files_with_durations.append((filepath, 60))
        batch_estimate = self.stats.estimate_batch_time(files_with_durations)
        self.file_estimates = batch_estimate["per_file"]
        self.total_estimated_time = batch_estimate["total_seconds"]
        estimate_str = self.time_formatter.format_duration(self.total_estimated_time)
        self.log(f"Ожидаемое время обработки: ~{estimate_str}")
        self.btn_start.setEnabled(False)
        self.btn_start.setText("ИДЕТ ОБРАБОТКА...")
        self.progress_bar_total.setValue(0)
        self.progress_bar_file.setValue(0)
        self._stage_start_time = 0.0
        self.detail_row.setVisible(True)
        self.btn_cancel.setEnabled(True)
        self.btn_cancel.setText("Отменить")
        self.btn_cancel.setVisible(True)
        self.btn_open_result.setVisible(False)
        self._last_result_dir = self.output_dir or os.path.dirname(self.files_to_process[0])
        self.lbl_file_counter.setText(f"Файл 1 / {self.total_files}")
        self.lbl_current_file.setText("")
        self.lbl_stage.setText("●  Подготовка…")
        self.lbl_status.setText(f"Оценка: ~{estimate_str}")
        self._set_status(f"Обработка {self.total_files} файлов…")
        self.progress_timer.start(500)
        num_speakers = None
        if self.enable_diarization:
            value = self.entry_num_speakers.value()
            if value > 0:
                num_speakers = value
        snapshot = {
            "num_speakers": num_speakers,
            "enable_diarization": self.enable_diarization,
            "selected_formats": self._get_selected_formats(),
            "output_dir": self.output_dir,
            "files": list(self.files_to_process),
            "start_time": self.start_time,
        }
        self._set_processing_controls_enabled(False)
        threading.Thread(target=self._process_files, kwargs={"snapshot": snapshot}, daemon=True).start()

    def _set_processing_controls_enabled(self, enabled: bool):
        self.cb_diarization.setEnabled(enabled)
        self.entry_num_speakers.setEnabled(enabled and self.enable_diarization)
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
        selected_formats = snapshot["selected_formats"]
        output_dir = snapshot["output_dir"]
        files = snapshot["files"]
        start_time = snapshot["start_time"]
        total_files = len(files)
        try:
            if not self.model_loader.load_model(self.log):
                self.signals.processing_finished.emit(False, "Не удалось загрузить модель")
                return
            processor = TranscriptionProcessor(
                self.model_loader, self.stats, self.log,
                progress_callback=self._on_file_progress
            )
            if enable_diarization:
                self.log(f"Количество спикеров: {num_speakers if num_speakers else 'автоопределение'}")
            files_processed = 0
            files_failed = 0
            failed_names = []
            time_spent = 0.0
            for i, filepath in enumerate(files):
                if self._cancel_requested:
                    self.log("Обработка отменена пользователем")
                    break
                try:
                    self.current_file_start_time = time.time()
                    self.signals.current_file_info.emit(os.path.basename(filepath))
                    file_output_dir = output_dir if output_dir else os.path.dirname(filepath)
                    result = processor.process_file(
                        filepath, file_output_dir, i, total_files,
                        enable_diarization=enable_diarization,
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
                    else:
                        files_failed += 1
                        failed_names.append(os.path.basename(filepath))
                    time_spent += result['total_time']
                except Exception as e:
                    files_failed += 1
                    failed_names.append(os.path.basename(filepath))
                    self.log(f"Ошибка при обработке файла {os.path.basename(filepath)}: {str(e)}")
                    continue
                finally:
                    self.files_processed = files_processed
                    self.time_spent = time_spent
            total_elapsed = time.time() - start_time
            self.log("=== ОБРАБОТКА ЗАВЕРШЕНА ===")
            self.log(f"Общее время обработки: {self.time_formatter.format_duration(total_elapsed)}")
            self.log(f"Успешно: {files_processed}/{total_files}" +
                     (f", с ошибками: {files_failed}" if files_failed else ""))
            cancelled = self._cancel_requested
            duration_str = self.time_formatter.format_duration(total_elapsed)
            if cancelled:
                message = f"Отменено. Обработано {files_processed}/{total_files} за {duration_str}"
            elif files_failed:
                message = f"Готово с ошибками: {files_processed}/{total_files} успешно за {duration_str}"
            else:
                message = f"Завершено за {duration_str}"
            if failed_names:
                shown = ", ".join(failed_names[:5])
                if len(failed_names) > 5:
                    shown += f" и ещё {len(failed_names) - 5}"
                message += f"\nНе удалось: {shown}\nПодробности — на вкладке «Журнал обработки»."
            success = (files_processed > 0) and (files_failed == 0) and not cancelled
            self.signals.processing_finished.emit(success, message)
        except Exception as e:
            self.log(f"Критическая ошибка: {str(e)}")
            self.signals.processing_finished.emit(False, f"Ошибка: {str(e)}")

    # ──────────────────────────────────────────────────────────────
    # Прогресс
    # ──────────────────────────────────────────────────────────────

    _STAGE_NAMES = {'conversion': 'Конвертация…', 'transcription': 'Распознавание речи…'}

    def _on_file_progress(self, stage: str, progress: float):
        self.signals.stage_update.emit(stage, progress)

    def _on_stage_update(self, stage: str, progress: float):
        if stage != self.current_stage:
            self.current_stage = stage
            self._stage_start_time = time.time()
        self.current_stage_progress = progress
        self._refresh_progress()

    def _estimate_file_progress(self) -> float:
        if self.current_file_start_time <= 0 or not self.files_to_process:
            return 0.0
        idx = min(self.files_processed, len(self.files_to_process) - 1)
        total_est = self.file_estimates.get(self.files_to_process[idx], 30) or 30
        band = self._CONVERSION_BAND
        stage_elapsed = (time.time() - self._stage_start_time) if self._stage_start_time else 0.0
        if self.current_stage == 'conversion':
            conv_est = max(1.0, total_est * band)
            return band * min(1.0, stage_elapsed / conv_est)
        if self.current_stage == 'transcription':
            trans_est = max(1.0, total_est * (1 - band))
            return band + (0.99 - band) * min(1.0, stage_elapsed / trans_est)
        elapsed = time.time() - self.current_file_start_time
        return min(0.99, elapsed / total_est)

    def _refresh_progress(self):
        if not self.is_processing or self.total_files == 0 or not self.files_to_process:
            return
        files_done = min(self.files_processed, self.total_files)
        file_progress = self._estimate_file_progress()
        overall = min(0.99, (files_done + file_progress) / self.total_files)
        self.progress_bar_total.setValue(int(overall * 100))
        self.progress_bar_file.setValue(int(file_progress * 100))
        current_idx = min(files_done + 1, self.total_files)
        self.lbl_file_counter.setText(f"Файл {current_idx} / {self.total_files}")
        stage_name = self._STAGE_NAMES.get(self.current_stage or '', 'Подготовка…')
        self.lbl_stage.setText(f"●  {stage_name}  {int(file_progress * 100)}%")
        remaining_files = self.total_files - files_done
        if remaining_files > 0:
            if files_done > 0 and self.time_spent > 0:
                avg_time = self.time_spent / files_done
            else:
                avg_time = self.file_estimates.get(self.files_to_process[0], 30)
            self.lbl_status.setText(f"Осталось: ~{self.time_formatter.format_duration(avg_time * remaining_files)}")

    def _update_progress_display(self):
        self._refresh_progress()

    def _update_total_progress(self, value: int):
        self.progress_bar_total.setValue(value)

    def _update_file_progress(self, value: int):
        self.progress_bar_file.setValue(value)

    def _update_current_file_info(self, info: str):
        self.current_stage = None
        self.current_stage_progress = 0.0
        display = info if len(info) <= 64 else f"…{info[-64:]}"
        self._current_filename = display
        self.lbl_current_file.setText(display)

    def _on_processing_finished(self, success: bool, message: str):
        self.is_processing = False
        self.progress_timer.stop()
        self.btn_start.setEnabled(True)
        self.btn_start.setText("ЗАПУСТИТЬ ОБРАБОТКУ")
        self.btn_cancel.setVisible(False)
        self.btn_cancel.setEnabled(True)
        self.btn_cancel.setText("Отменить")
        self.progress_bar_total.setValue(100 if success else self.progress_bar_total.value())
        self.progress_bar_file.setValue(100 if success else self.progress_bar_file.value())
        self.lbl_stage.setText("✓  Готово" if success else "✕  Остановлено")
        self.lbl_status.setText(message.split("\n")[0])
        self._set_status(message.split("\n")[0])
        self._set_processing_controls_enabled(True)

        has_results = bool(self._last_result_dir) and os.path.isdir(self._last_result_dir)
        self.btn_open_result.setVisible(has_results)

        box = QMessageBox(self)
        box.setWindowTitle("Готово" if success else "Завершено")
        box.setIcon(QMessageBox.Icon.Information if success else QMessageBox.Icon.Warning)
        box.setText(("Обработка завершена!\n" + message) if success else message)
        open_btn = None
        if has_results:
            open_btn = box.addButton("Открыть папку с результатами", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if open_btn is not None and box.clickedButton() is open_btn:
            self._open_results_folder()

    def closeEvent(self, event):
        if self.is_processing or self.is_downloading:
            reply = QMessageBox.question(
                self, "Внимание",
                "Идёт обработка/загрузка. Закрыть приложение и прервать её?",
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
        self._save_geometry()
        self.app_logger.log_session_end()
        event.accept()

    # ──────────────────────────────────────────────────────────────
    # Ошибки загрузки модели
    # ──────────────────────────────────────────────────────────────

    def _show_model_error(self, message: str):
        QMessageBox.warning(self, "Ошибка загрузки", message)


def run_qt_app(app=None):
    """Запускает приложение на PyQt6.

    app: уже созданный QApplication (используется на этапе выбора устройства).
    """
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('GigaAM.Transcriber.v3')
        except Exception:
            pass

    app = app or QApplication.instance() or QApplication(sys.argv)
    app.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont))

    icon_path = os.path.join(
        getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
        'icon.ico'
    )
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    window = GigaTranscriberQtApp()
    window.show()

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
