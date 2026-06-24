"""
Главное окно приложения GigaAM v3 Transcriber на PyQt6
Строгий профессиональный дизайн без ярких цветов
"""

import os
import sys
import threading
import time

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QDragEnterEvent, QDropEvent, QFont, QIcon, QPalette
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
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..config import APP_TITLE, MEDIA_EXTENSIONS, OUTPUT_FORMATS, STATS_FILE
from ..core import ModelLoader, TranscriptionProcessor
from ..utils import AppLogger, AudioConverter, MediaDownloader, ProcessingStats, TimeFormatter, UserSettings


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

    def _toggle_theme(self):
        self._theme = "dark" if self._theme == "light" else "light"
        self.user_settings.settings["theme"] = self._theme
        self.user_settings._save_settings()
        self._apply_theme()
        self._btn_theme.setText(self._colors()["theme_btn"])

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
        rad_f = 11   # file bar radius (height=22 // 2)
        rad_s = 8    # small bar radius (height=16 // 2)

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
                border-radius: 6px;
                background-color: {c["bg"]};
            }}
            QTabBar::tab {{
                background-color: {c["tab_bg"]};
                color: {c["tab_text"]};
                border: 1px solid {c["border"]};
                border-bottom: none;
                border-radius: 5px 5px 0 0;
                padding: 6px 18px;
                font-size: 11pt;
                margin-right: 2px;
            }}
            QTabBar::tab:selected {{
                background-color: {c["tab_sel_bg"]};
                color: {c["tab_sel_text"]};
                font-weight: bold;
                border-bottom: 2px solid {c["tab_accent"]};
            }}
            QTabBar::tab:hover:!selected {{
                background-color: {c["tab_hover"]};
            }}
            QGroupBox {{
                font-weight: bold;
                font-size: 11pt;
                border: 1px solid {c["border"]};
                border-radius: 8px;
                margin-top: 14px;
                padding-top: 6px;
                background-color: {c["bg_card"]};
                color: {c["text"]};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 14px;
                padding: 2px 8px;
                color: {c["text_sub"]};
                background-color: {c["bg_card"]};
            }}
            QPushButton {{
                background-color: {c["btn_bg"]};
                border: 1px solid {c["btn_border"]};
                border-radius: 6px;
                padding: 6px 16px;
                color: {c["btn_text"]};
                font-size: 10pt;
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
                font-size: 13pt;
                font-weight: bold;
                border: none;
                border-radius: 8px;
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
                font-size: 10pt;
                font-weight: bold;
                border: 1px solid {c["clear_border"]};
                border-radius: 6px;
            }}
            QPushButton#clear_button:hover {{
                background-color: {c["clear_hover_bg"]};
                border: 1px solid {c["clear_hover_border"]};
                color: {c["clear_hover_text"]};
            }}
            QPushButton#theme_button {{
                background-color: transparent;
                border: 1px solid {c["border"]};
                border-radius: 6px;
                padding: 2px 8px;
                font-size: 16pt;
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
                font-size: 10pt;
            }}
            QProgressBar::chunk {{
                background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 {r}, stop:1 {r2});
                border-radius: {rad_f}px;
            }}
            QLineEdit {{
                border: 1px solid {c["btn_border"]};
                border-radius: 6px;
                padding: 4px 10px;
                background-color: {c["input_bg"]};
                color: {c["text"]};
                selection-background-color: {c["input_sel"]};
                font-size: 10pt;
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
                border-radius: 6px;
                background-color: {c["input_bg"]};
                color: {c["text"]};
                selection-background-color: {c["input_sel"]};
                font-size: 10pt;
            }}
            QCheckBox {{
                spacing: 8px;
                color: {c["text_sub"]};
                font-size: 10pt;
            }}
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border: 1.5px solid {c["btn_border"]};
                border-radius: 4px;
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
                color: {c["text_sub"]};
                font-size: 10pt;
            }}
            QScrollBar:vertical {{
                background: {c["scroll_bg"]};
                width: 8px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: {c["scroll_handle"]};
                border-radius: 4px;
                min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {c["scroll_handle_hover"]};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
            QScrollBar:horizontal {{
                background: {c["scroll_bg"]};
                height: 8px;
                border-radius: 4px;
            }}
            QScrollBar::handle:horizontal {{
                background: {c["scroll_handle"]};
                border-radius: 4px;
                min-width: 30px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background: {c["scroll_handle_hover"]};
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
            #progress_card {{
                background-color: {c["bg_card"]};
                border: 1px solid {c["border"]};
                border-radius: 8px;
            }}
            #progress_card QLabel {{
                border: none;
                background: transparent;
            }}
        """)

        # Обновляем динамические стили прогресс-баров файла (тонкий)
        if hasattr(self, 'progress_bar_file'):
            self.progress_bar_file.setStyleSheet(
                f"QProgressBar {{ border: none; border-radius: {rad_s}px;"
                f"  background-color: {c['progress_bg']}; text-align: center;"
                f"  color: {c['text']}; font-size: 8pt; font-weight: 600; }}"
                f"QProgressBar::chunk {{ border-radius: {rad_s}px;"
                f"  background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                f"  stop:0 {r}, stop:1 {r2}); }}"
            )
        if hasattr(self, 'progress_bar_total'):
            self.progress_bar_total.setStyleSheet(
                f"QProgressBar {{ border: none; border-radius: {rad_f}px;"
                f"  background-color: {c['progress_bg']}; text-align: center;"
                f"  color: {c['text']}; font-size: 10pt; font-weight: 600; }}"
                f"QProgressBar::chunk {{ border-radius: {rad_f}px;"
                f"  background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                f"  stop:0 {r}, stop:1 {r2}); }}"
            )
        if hasattr(self, 'progress_upload'):
            self.progress_upload.setStyleSheet(
                f"QProgressBar {{ border: none; background-color: {c['progress_bg']};"
                f"  border-radius: 3px; }}"
                f"QProgressBar::chunk {{ background-color: {c['accent']}; border-radius: 3px; }}"
            )
        if hasattr(self, 'lbl_file_counter'):
            self.lbl_file_counter.setStyleSheet(f"color: {c['accent']}; font-size: 11pt; font-weight: bold;")
        if hasattr(self, 'lbl_status'):
            self.lbl_status.setStyleSheet(
                f"color: {c['status_text']}; font-size: 10pt; font-weight: bold;"
                f"background-color: {c['status_bg']}; border-radius: 6px; padding: 2px;"
            )
        if hasattr(self, 'lbl_stage'):
            self.lbl_stage.setStyleSheet(f"color: {c['text_sub']}; font-size: 9pt; font-weight: 600;")
        if hasattr(self, 'lbl_current_file'):
            self.lbl_current_file.setStyleSheet(f"color: {c['text_mute2']}; font-size: 9pt;")

    # ──────────────────────────────────────────────────────────────
    # UI
    # ──────────────────────────────────────────────────────────────

    def _init_ui(self):
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(940, 560)
        self.resize(1040, 850)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(16, 12, 16, 12)
        root_layout.setSpacing(8)
        self.setCentralWidget(root)

        # Заголовок + кнопка темы
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)

        title_label = QLabel("GigaAM v3: Транскрибация")
        title_label.setFont(QFont("Arial", 18, QFont.Weight.Bold))
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setFixedHeight(40)
        header_row.addWidget(title_label, 1)

        self._btn_theme = QPushButton(self._colors()["theme_btn"])
        self._btn_theme.setObjectName("theme_button")
        self._btn_theme.setFixedSize(42, 36)
        self._btn_theme.setToolTip("Переключить тему")
        self._btn_theme.clicked.connect(self._toggle_theme)
        header_row.addWidget(self._btn_theme)

        root_layout.addLayout(header_row)

        tabs = QTabWidget()
        root_layout.addWidget(tabs, 1)

        # ── Вкладка «Обработка» ──
        content_widget = QWidget()
        main_layout = QVBoxLayout(content_widget)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        main_layout.addWidget(self._create_files_group())
        main_layout.addWidget(self._create_output_group())
        main_layout.addWidget(self._create_diarization_group())
        main_layout.addWidget(self._create_formats_group())

        self.btn_start = QPushButton("ЗАПУСТИТЬ ОБРАБОТКУ")
        self.btn_start.setObjectName("start_button")
        self.btn_start.setFixedHeight(52)
        self.btn_start.clicked.connect(self._start_processing_thread)
        main_layout.addWidget(self.btn_start)

        self._create_progress_section(main_layout)

        self.btn_clear = QPushButton("ОЧИСТИТЬ ВСЕ")
        self.btn_clear.setObjectName("clear_button")
        self.btn_clear.setFixedHeight(40)
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
        log_layout.setContentsMargins(8, 8, 8, 8)
        log_layout.setSpacing(6)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 11))
        self.log_text.setMinimumHeight(160)
        log_layout.addWidget(self.log_text, 1)
        tabs.addTab(log_tab, "Журнал обработки")
        self.tabs = tabs

        self._apply_theme()

    _ACCENT_LIGHT = "#3b82f6"
    _CONVERSION_BAND = 0.15

    def _make_progress_bar(self, height: int, font_pt: int) -> QProgressBar:
        c = self._colors()
        bar = QProgressBar()
        bar.setFixedHeight(height)
        bar.setTextVisible(True)
        bar.setRange(0, 100)
        radius = height // 2
        r, r2 = c["progress_chunk"], c["progress_chunk2"]
        bar.setStyleSheet(
            f"QProgressBar {{ border: none; border-radius: {radius}px;"
            f"  background-color: {c['progress_bg']}; text-align: center; color: {c['text']};"
            f"  font-size: {font_pt}pt; font-weight: 600; }}"
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
        frame_layout.setContentsMargins(16, 12, 16, 12)
        frame_layout.setSpacing(6)

        head_row = QHBoxLayout()
        lbl_overall = QLabel("Общий прогресс")
        lbl_overall.setStyleSheet(f"color: {c['text_sub']}; font-size: 11pt; font-weight: bold;")
        head_row.addWidget(lbl_overall)
        head_row.addStretch()
        self.lbl_file_counter = QLabel("")
        self.lbl_file_counter.setStyleSheet(f"color: {c['accent']}; font-size: 11pt; font-weight: bold;")
        head_row.addWidget(self.lbl_file_counter)
        frame_layout.addLayout(head_row)

        self.progress_bar_total = self._make_progress_bar(height=22, font_pt=10)
        frame_layout.addWidget(self.progress_bar_total)

        self.detail_row = QWidget()
        detail_layout = QHBoxLayout(self.detail_row)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(10)
        self.lbl_stage = QLabel("")
        self.lbl_stage.setStyleSheet(f"color: {c['text_sub']}; font-size: 9pt; font-weight: 600;")
        detail_layout.addWidget(self.lbl_stage)
        detail_layout.addStretch()
        self.lbl_current_file = QLabel("")
        self.lbl_current_file.setStyleSheet(f"color: {c['text_mute2']}; font-size: 9pt;")
        detail_layout.addWidget(self.lbl_current_file)
        frame_layout.addWidget(self.detail_row)
        self.detail_row.setVisible(False)

        self.progress_bar_file = self._make_progress_bar(height=16, font_pt=8)
        frame_layout.addWidget(self.progress_bar_file)

        self.lbl_status = QLabel("Готов к работе")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setFixedHeight(28)
        self.lbl_status.setStyleSheet(
            f"color: {c['status_text']}; font-size: 10pt; font-weight: bold;"
            f"background-color: {c['status_bg']}; border-radius: 6px; padding: 2px;"
        )
        frame_layout.addWidget(self.lbl_status)

        parent_layout.addWidget(progress_frame)

    def _create_files_group(self) -> QGroupBox:
        group = QGroupBox("1. Выбор файлов")
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(12, 20, 12, 10)
        main_layout.setSpacing(10)

        row1 = QHBoxLayout()
        row1.setSpacing(10)
        btn_select_files = QPushButton("Выбрать файлы")
        btn_select_files.clicked.connect(self._select_files)
        btn_select_files.setFixedHeight(36)
        btn_select_files.setMinimumWidth(180)
        row1.addWidget(btn_select_files)

        self.lbl_files_count = QLabel("Файлы не выбраны")
        self.lbl_files_count.setStyleSheet(f"color: {self._colors()['text_mute']};")
        self.lbl_files_count.setFixedWidth(180)
        row1.addWidget(self.lbl_files_count)

        self.input_path = QLineEdit()
        self.input_path.setPlaceholderText("Ссылка на медиа")
        self.input_path.setFixedHeight(24)
        self.input_path.setMinimumWidth(220)
        row1.addWidget(self.input_path)

        self.btn_upload = QPushButton("Загрузить")
        self.btn_upload.setFixedHeight(36)
        self.btn_upload.setMinimumWidth(100)
        self.btn_upload.clicked.connect(self._start_download)
        row1.addWidget(self.btn_upload)

        self.progress_upload = QProgressBar()
        self.progress_upload.setFixedHeight(24)
        self.progress_upload.setFixedWidth(90)
        self.progress_upload.setValue(0)
        self.progress_upload.setTextVisible(True)
        row1.addWidget(self.progress_upload)
        row1.addStretch()
        main_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(10)
        btn_select_folder = QPushButton("Выбрать папку с файлами")
        btn_select_folder.clicked.connect(self._select_files_folder)
        btn_select_folder.setFixedHeight(36)
        btn_select_folder.setMinimumWidth(180)
        row2.addWidget(btn_select_folder)

        self.lbl_input_folder = QLabel("Папка не выбрана")
        self.lbl_input_folder.setStyleSheet(f"color: {self._colors()['text_mute']};")
        self.lbl_input_folder.setFixedWidth(180)
        row2.addWidget(self.lbl_input_folder)
        row2.addStretch()
        main_layout.addLayout(row2)

        group.setLayout(main_layout)
        return group

    def _create_output_group(self) -> QGroupBox:
        group = QGroupBox("2. Папка сохранения результатов")
        layout = QHBoxLayout()
        layout.setContentsMargins(12, 20, 12, 10)
        layout.setSpacing(12)
        btn_output = QPushButton("Выбрать папку")
        btn_output.clicked.connect(self._select_output_folder)
        btn_output.setMinimumWidth(220)
        btn_output.setFixedHeight(36)
        layout.addWidget(btn_output)
        self.lbl_output_folder = QLabel("Папка не выбрана (по умолчанию - рядом с файлом)")
        self.lbl_output_folder.setStyleSheet(f"color: {self._colors()['text_mute']};")
        layout.addWidget(self.lbl_output_folder, 1)
        group.setLayout(layout)
        return group

    def _create_diarization_group(self) -> QGroupBox:
        group = QGroupBox("3. Диаризация спикеров")
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 20, 12, 10)
        layout.setSpacing(8)
        self.cb_diarization = QCheckBox("Включить диаризацию спикеров")
        self.cb_diarization.stateChanged.connect(self._toggle_diarization)
        layout.addWidget(self.cb_diarization)
        speakers_layout = QHBoxLayout()
        speakers_layout.setSpacing(12)
        speakers_layout.addWidget(QLabel("Кол-во спикеров:"))
        self.entry_num_speakers = QLineEdit()
        self.entry_num_speakers.setPlaceholderText("Пусто = автоопределение")
        self.entry_num_speakers.setEnabled(False)
        self.entry_num_speakers.setFixedHeight(32)
        self.entry_num_speakers.setMinimumWidth(250)
        self.entry_num_speakers.setMaximumWidth(350)
        speakers_layout.addWidget(self.entry_num_speakers)
        speakers_layout.addStretch()
        layout.addLayout(speakers_layout)
        info_label = QLabel("Автоматическое определение спикеров (требуется HF_TOKEN)")
        info_label.setStyleSheet(f"color: {self._colors()['text_mute2']}; font-size: 9pt;")
        layout.addWidget(info_label)
        group.setLayout(layout)
        return group

    def _create_formats_group(self) -> QGroupBox:
        group = QGroupBox("4. Форматы вывода")
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 20, 12, 10)
        layout.setSpacing(6)
        self.format_checkboxes = {}

        row1 = QHBoxLayout()
        row1.setSpacing(20)
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
        row2.setSpacing(20)
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
        dlg.setMinimumWidth(520)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(10)
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
                with open(env_path, 'r') as f:
                    lines = [l for l in f.readlines() if not l.startswith('HF_TOKEN=')]
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
            self.entry_num_speakers.clear()
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
        self.lbl_files_count.setText(f"Выбрано файлов: {len(self.files_to_process)}")
        self.lbl_files_count.setStyleSheet(f"color: {self._colors()['text_sub']};")
        self.log(f"Добавлено в очередь: {len(unique_files)} файлов")
        for f in unique_files:
            self.log(f" + {os.path.basename(f)}")

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
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
                self.lbl_files_count.setText(f"Выбрано файлов: {len(files)}")
                self.lbl_files_count.setStyleSheet(f"color: {self._colors()['text_sub']};")
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
        display_path = path if len(path) < 60 else f"...{path[-60:]}"
        self.lbl_input_folder.setText(display_path)
        self.lbl_input_folder.setStyleSheet(f"color: {self._colors()['text_sub']};")

    def _update_output_dir_label(self, path: str):
        display_path = path if len(path) < 60 else f"...{path[-60:]}"
        self.lbl_output_folder.setText(display_path)
        self.lbl_output_folder.setStyleSheet(f"color: {self._colors()['text_sub']};")

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
        self.lbl_status.setText("Загрузка медиа по ссылке...")
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
        self.log_text.clear()
        self.progress_bar_total.setValue(0)
        self.progress_bar_file.setValue(0)
        self.progress_upload.setValue(0)
        self.lbl_current_file.setText("")
        self.lbl_stage.setText("")
        self.lbl_file_counter.setText("")
        self.detail_row.setVisible(False)
        self.lbl_status.setText("Готов к работе")
        c = self._colors()
        self.lbl_files_count.setText("Файлы не выбраны")
        self.lbl_files_count.setStyleSheet(f"color: {c['text_mute']};")
        self.lbl_input_folder.setText("Папка не выбрана")
        self.lbl_input_folder.setStyleSheet(f"color: {c['text_mute']};")
        self.lbl_output_folder.setText("Папка не выбрана (по умолчанию - рядом с файлом)")
        self.lbl_output_folder.setStyleSheet(f"color: {c['text_mute']};")
        self.input_path.clear()
        self.btn_start.setEnabled(True)
        self.btn_upload.setEnabled(True)
        self.log("Все настройки сброшены")

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
        self.lbl_file_counter.setText(f"Файл 1 / {self.total_files}")
        self.lbl_current_file.setText("")
        self.lbl_stage.setText("●  Подготовка…")
        self.lbl_status.setText(f"Оценка: ~{estimate_str}")
        self.progress_timer.start(500)
        num_speakers = None
        if self.enable_diarization:
            try:
                speakers_text = self.entry_num_speakers.text().strip()
                if speakers_text:
                    value = int(speakers_text)
                    if value > 0:
                        num_speakers = value
                    else:
                        self.log("ПРЕДУПРЕЖДЕНИЕ: число спикеров должно быть > 0, используется автоопределение")
            except ValueError:
                self.log("ПРЕДУПРЕЖДЕНИЕ: некорректное число спикеров, используется автоопределение")
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
                    time_spent += result['total_time']
                except Exception as e:
                    files_failed += 1
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
        self.progress_bar_total.setValue(100 if success else self.progress_bar_total.value())
        self.progress_bar_file.setValue(100 if success else self.progress_bar_file.value())
        self.lbl_stage.setText("✓  Готово" if success else "✕  Остановлено")
        self.lbl_status.setText(message)
        self._set_processing_controls_enabled(True)
        if success:
            QMessageBox.information(self, "Готово", f"Обработка завершена!\n{message}")
        else:
            QMessageBox.warning(self, "Завершено", message)

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
        self.app_logger.log_session_end()
        event.accept()

    # ──────────────────────────────────────────────────────────────
    # Ошибки загрузки модели
    # ──────────────────────────────────────────────────────────────

    def _show_model_error(self, message: str):
        QMessageBox.warning(self, "Ошибка загрузки", message)


def run_qt_app():
    """Запускает приложение на PyQt6"""
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(u'GigaAM.Transcriber.v3')
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setFont(QFont("Arial", 12))

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
