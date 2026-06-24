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
    QDialog,
    QDialogButtonBox,
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

        # Переменные состояния
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
        self._stage_start_time = 0.0   # время начала текущего этапа (для интерполяции)
        self._current_filename = ""    # имя обрабатываемого файла (для подписи)
        self.is_downloading = False
        self.start_processing_after_download = False

        # Настройки диаризации
        self.enable_diarization = False
        self.num_speakers = None

        # Настройки выходных форматов (txt и txt_timecodes включены по умолчанию)
        self.output_formats = {
            'txt': True,
            'txt_timecodes': True,
            'txt_diarize': False,
            'txt_diarize_timecodes': False,
            'md': False,
            'srt': False,
            'vtt': False,
        }

        # Инициализация модулей
        self.app_logger = AppLogger()
        self.app_logger.log_session_start()
        self.model_loader = ModelLoader()
        self.stats = ProcessingStats(STATS_FILE)
        self.time_formatter = TimeFormatter()
        self.user_settings = UserSettings()
        self.media_downloader = MediaDownloader()

        # Сигналы для потока обработки
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

        # Таймер для обновления прогресса
        self.progress_timer = QTimer()
        self.progress_timer.timeout.connect(self._update_progress_display)

        # Загружаем сохраненные пути
        saved_output_dir = self.user_settings.get_last_output_dir()
        saved_input_dir = self.user_settings.get_last_files_dir()

        if saved_output_dir:
            self.output_dir = saved_output_dir
        if saved_input_dir:
            self.input_dir = saved_input_dir

        # Инициализация интерфейса
        self._init_ui()
        # Включаем приём перетаскивания файлов (на всё окно, в т.ч. на кнопку «Выбрать файлы»)
        self.setAcceptDrops(True)

        # Обновляем метки папок
        if saved_output_dir:
            self._update_output_dir_label(saved_output_dir)
        if saved_input_dir:
            self._update_input_dir_label(saved_input_dir)

        # Очистка старых логов
        self.app_logger.cleanup_old_logs()

    def _init_ui(self):
        """Инициализация интерфейса"""
        self.setWindowTitle(APP_TITLE)
        # Стартовый размер подобран так, чтобы вкладка «Обработка» помещалась целиком
        # без прокрутки. Минимум — заметно меньше: если пользователь уменьшит окно,
        # контент вкладки уходит в прокрутку (QScrollArea), а не наезжает друг на друга.
        self.setMinimumSize(940, 560)
        self.resize(1040, 850)

        # Корневой виджет: заголовок + вкладки
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(16, 12, 16, 12)
        root_layout.setSpacing(8)
        self.setCentralWidget(root)

        # Заголовок (общий для всех вкладок)
        title_label = QLabel("GigaAM v3: Транскрибация")
        title_label.setFont(QFont("Arial", 18, QFont.Weight.Bold))
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setFixedHeight(40)
        root_layout.addWidget(title_label)

        tabs = QTabWidget()
        root_layout.addWidget(tabs, 1)

        # ===== Вкладка «Обработка» =====
        content_widget = QWidget()
        main_layout = QVBoxLayout(content_widget)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # Блок выбора файлов
        files_group = self._create_files_group()
        main_layout.addWidget(files_group)

        # Блок папки сохранения
        output_group = self._create_output_group()
        main_layout.addWidget(output_group)

        # Блок диаризации
        diarization_group = self._create_diarization_group()
        main_layout.addWidget(diarization_group)

        # Блок форматов вывода
        formats_group = self._create_formats_group()
        main_layout.addWidget(formats_group)

        # Кнопка запуска
        self.btn_start = QPushButton("ЗАПУСТИТЬ ОБРАБОТКУ")
        self.btn_start.setObjectName("start_button")
        self.btn_start.setFixedHeight(52)
        self.btn_start.clicked.connect(self._start_processing_thread)
        main_layout.addWidget(self.btn_start)

        # Блок прогресса (отдельная секция без QGroupBox)
        self._create_progress_section(main_layout)

        # Кнопка очистки
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

        # ===== Вкладка «Журнал обработки» =====
        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        log_layout.setContentsMargins(8, 8, 8, 8)
        log_layout.setSpacing(6)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 11))
        self.log_text.setMinimumHeight(160)
        log_layout.addWidget(self.log_text, 1)  # Растягивается

        tabs.addTab(log_tab, "Журнал обработки")
        self.tabs = tabs

        # Применяем строгую цветовую схему после создания всех элементов
        self._apply_strict_style()

    def _apply_strict_style(self):
        """Применяет строгую профессиональную темную цветовую схему"""
        # Определяем темную палитру
        palette = QPalette()

        # Основные цвета - темная тема
        palette.setColor(QPalette.ColorRole.Window, QColor(45, 45, 48))           # Темно-серый фон
        palette.setColor(QPalette.ColorRole.WindowText, QColor(220, 220, 220))    # Светлый текст
        palette.setColor(QPalette.ColorRole.Base, QColor(30, 30, 30))             # Темный фон для полей ввода
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(40, 40, 42))    # Альтернативный фон
        palette.setColor(QPalette.ColorRole.Text, QColor(220, 220, 220))          # Светлый текст
        palette.setColor(QPalette.ColorRole.Button, QColor(60, 60, 62))           # Кнопки
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(220, 220, 220))    # Текст кнопок

        # Применяем палитру
        self.setPalette(palette)

        # Применяем стили через stylesheet (темная тема)
        self.setStyleSheet("""
            QMainWindow {
                background-color: #2d2d30;
            }
            QGroupBox {
                font-weight: bold;
                font-size: 12pt;
                border: 1px solid #4a4a4e;
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 6px;
                background-color: #38383b;
                color: #dcdcdc;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 12px;
                padding: 2px 6px;
                color: #e0e0e0;
            }
            QPushButton {
                background-color: #4a4a4d;
                border: 1px solid #5a5a5d;
                border-radius: 4px;
                padding: 6px 14px;
                color: #e0e0e0;
                font-size: 11pt;
            }
            QPushButton:hover {
                background-color: #58585b;
                border: 1px solid #6a6a6d;
            }
            QPushButton:pressed {
                background-color: #3a3a3d;
            }
            QPushButton:disabled {
                background-color: #3a3a3c;
                color: #707070;
                border: 1px solid #4a4a4c;
            }
            QPushButton#start_button {
                background-color: #505050;
                color: #ffffff;
                font-size: 14pt;
                font-weight: bold;
                border: 1px solid #606060;
            }
            QPushButton#start_button:hover {
                background-color: #606060;
                border: 1px solid #707070;
            }
            QPushButton#start_button:pressed {
                background-color: #404040;
            }
            QPushButton#start_button:disabled {
                background-color: #3a3a3c;
                color: #707070;
            }
            QPushButton#clear_button {
                background-color: #5a5a5c;
                color: #ffffff;
                font-size: 11pt;
                font-weight: bold;
                border: 1px solid #6a6a6c;
            }
            QPushButton#clear_button:hover {
                background-color: #6a6a6c;
            }
            QProgressBar {
                border: 1px solid #555555;
                border-radius: 4px;
                text-align: center;
                background-color: #252528;
                color: #e0e0e0;
                font-size: 10pt;
            }
            QProgressBar::chunk {
                background-color: #5a5a5d;
                border-radius: 3px;
            }
            QLineEdit {
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 4px 8px;
                background-color: #1e1e1e;
                color: #dcdcdc;
                selection-background-color: #505050;
                font-size: 11pt;
            }
            QLineEdit:disabled {
                background-color: #2a2a2c;
                color: #707070;
            }
            QTextEdit {
                border: 1px solid #4a4a4e;
                border-radius: 4px;
                background-color: #1e1e1e;
                color: #dcdcdc;
                selection-background-color: #505050;
                font-size: 11pt;
            }
            QCheckBox {
                spacing: 8px;
                color: #dcdcdc;
                font-size: 11pt;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border: 1px solid #5a5a5d;
                border-radius: 3px;
                background-color: #2a2a2c;
            }
            QCheckBox::indicator:checked {
                background-color: #606060;
                border: 1px solid #707070;
            }
            QCheckBox::indicator:hover {
                border: 1px solid #707070;
            }
            QLabel {
                color: #dcdcdc;
                font-size: 11pt;
            }
        """)

    def _create_files_group(self) -> QGroupBox:
        group = QGroupBox("1. Выбор файлов")
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(12, 20, 12, 10)
        main_layout.setSpacing(10)

        # --- 1 строка: Выбрать файлы + Лейбл + Input + Прогресс ---
        row1 = QHBoxLayout()
        row1.setSpacing(10)

        # Кнопка "Выбрать файлы"
        btn_select_files = QPushButton("Выбрать файлы")
        btn_select_files.clicked.connect(self._select_files)
        btn_select_files.setFixedHeight(36)
        btn_select_files.setMinimumWidth(180)
        row1.addWidget(btn_select_files)

        # Лейбл для выбранных файлов
        self.lbl_files_count = QLabel("Файлы не выбраны")
        self.lbl_files_count.setStyleSheet("color: #909090;")
        self.lbl_files_count.setFixedWidth(180)
        row1.addWidget(self.lbl_files_count)

        # Поле ввода ссылки
        self.input_path = QLineEdit()
        self.input_path.setPlaceholderText("Ссылка на медиа")
        self.input_path.setFixedHeight(24)
        self.input_path.setMinimumWidth(220)
        row1.addWidget(self.input_path)

        # Кнопка загрузки ссылки
        self.btn_upload = QPushButton("Загрузить")
        self.btn_upload.setFixedHeight(36)
        self.btn_upload.setMinimumWidth(100)
        self.btn_upload.clicked.connect(self._start_download)
        row1.addWidget(self.btn_upload)

        # Прогресс-бар
        self.progress_upload = QProgressBar()
        self.progress_upload.setFixedHeight(24)
        self.progress_upload.setFixedWidth(90)
        self.progress_upload.setValue(0)
        self.progress_upload.setTextVisible(True)
        self.progress_upload.setStyleSheet("""
            QProgressBar {
                border: none;
                background-color: #2a2a2c;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background-color: #7a7a7d;
                border-radius: 3px;
            }
        """)
        row1.addWidget(self.progress_upload)

        row1.addStretch()
        main_layout.addLayout(row1)

        # 2 строка: Выбрать папку + Лейбл
        row2 = QHBoxLayout()
        row2.setSpacing(10)

        # Кнопка выбора папки
        btn_select_folder = QPushButton("Выбрать папку с файлами")
        btn_select_folder.clicked.connect(self._select_files_folder)
        btn_select_folder.setFixedHeight(36)
        btn_select_folder.setMinimumWidth(180)
        row2.addWidget(btn_select_folder)

        # Лейбл выбранной папки
        self.lbl_input_folder = QLabel("Папка не выбрана")
        self.lbl_input_folder.setStyleSheet("color: #909090;")
        self.lbl_input_folder.setFixedWidth(180)
        row2.addWidget(self.lbl_input_folder)

        row2.addStretch()
        main_layout.addLayout(row2)

        group.setLayout(main_layout)
        return group

    def _create_output_group(self) -> QGroupBox:
        """Создает блок папки сохранения"""
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
        self.lbl_output_folder.setStyleSheet("color: #909090;")
        layout.addWidget(self.lbl_output_folder, 1)

        group.setLayout(layout)
        return group

    def _create_diarization_group(self) -> QGroupBox:
        """Создает блок настроек диаризации"""
        group = QGroupBox("3. Диаризация спикеров")
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 20, 12, 10)
        layout.setSpacing(8)

        # Чекбокс включения диаризации
        self.cb_diarization = QCheckBox("Включить диаризацию спикеров")
        self.cb_diarization.stateChanged.connect(self._toggle_diarization)
        layout.addWidget(self.cb_diarization)

        # Поле ввода количества спикеров
        speakers_layout = QHBoxLayout()
        speakers_layout.setSpacing(12)
        lbl_speakers = QLabel("Кол-во спикеров:")
        speakers_layout.addWidget(lbl_speakers)

        self.entry_num_speakers = QLineEdit()
        self.entry_num_speakers.setPlaceholderText("Пусто = автоопределение")
        self.entry_num_speakers.setEnabled(False)
        self.entry_num_speakers.setFixedHeight(32)
        self.entry_num_speakers.setMinimumWidth(250)
        self.entry_num_speakers.setMaximumWidth(350)
        speakers_layout.addWidget(self.entry_num_speakers)
        speakers_layout.addStretch()

        layout.addLayout(speakers_layout)

        # Информация
        info_label = QLabel("Автоматическое определение спикеров (требуется HF_TOKEN)")
        info_label.setStyleSheet("color: #909090; font-size: 9pt;")
        layout.addWidget(info_label)

        group.setLayout(layout)
        return group

    def _create_formats_group(self) -> QGroupBox:
        """Создает блок выбора форматов вывода"""
        group = QGroupBox("4. Форматы вывода")
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 20, 12, 10)
        layout.setSpacing(6)

        self.format_checkboxes = {}

        # Первый ряд: четыре текстовых варианта
        row1 = QHBoxLayout()
        row1.setSpacing(20)
        txt_fmts = ['txt', 'txt_timecodes', 'txt_diarize', 'txt_diarize_timecodes']
        for fmt in txt_fmts:
            label = OUTPUT_FORMATS[fmt]
            cb = QCheckBox(label)
            cb.setChecked(fmt in ('txt', 'txt_timecodes'))
            # Диаризационные варианты недоступны пока диаризация выключена
            if fmt in ('txt_diarize', 'txt_diarize_timecodes'):
                cb.setEnabled(False)
            cb.stateChanged.connect(lambda state, f=fmt: self._toggle_format(f))
            row1.addWidget(cb)
            self.format_checkboxes[fmt] = cb
        row1.addStretch()
        layout.addLayout(row1)

        # Второй ряд: дополнительные форматы
        row2 = QHBoxLayout()
        row2.setSpacing(20)
        for fmt in ('md', 'srt', 'vtt'):
            label = OUTPUT_FORMATS[fmt]
            cb = QCheckBox(label)
            cb.setChecked(False)
            cb.stateChanged.connect(lambda state, f=fmt: self._toggle_format(f))
            row2.addWidget(cb)
            self.format_checkboxes[fmt] = cb
        row2.addStretch()
        layout.addLayout(row2)

        group.setLayout(layout)
        return group

    # Акцентный цвет прогресса и доля шкалы файла, отводимая на конвертацию
    _ACCENT = "#3d7eff"
    _CONVERSION_BAND = 0.15  # 0..15% шкалы файла — конвертация, 15..99% — распознавание

    def _make_progress_bar(self, height: int, font_pt: int) -> QProgressBar:
        """Создаёт стилизованную полосу прогресса (единый стиль для всех баров)."""
        bar = QProgressBar()
        bar.setFixedHeight(height)
        bar.setTextVisible(True)
        bar.setRange(0, 100)
        radius = height // 2
        bar.setStyleSheet(
            f"QProgressBar {{ border: none; border-radius: {radius}px;"
            f"  background-color: #2a2a2e; text-align: center; color: #f0f0f0;"
            f"  font-size: {font_pt}pt; font-weight: 600; }}"
            f"QProgressBar::chunk {{ border-radius: {radius}px;"
            f"  background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            f"  stop:0 {self._ACCENT}, stop:1 #2f6bdb); }}"
        )
        return bar

    def _create_progress_section(self, parent_layout):
        """Создаёт секцию прогресса (без QGroupBox — чтобы не было проблем с отступами)."""
        # Контейнер-карточка прогресса
        progress_frame = QFrame()
        progress_frame.setObjectName("progress_card")
        progress_frame.setStyleSheet(
            "#progress_card { background-color: #313135; border: 1px solid #45454a;"
            "  border-radius: 8px; }"
            "#progress_card QLabel { border: none; background: transparent; }"
        )
        frame_layout = QVBoxLayout(progress_frame)
        frame_layout.setContentsMargins(16, 12, 16, 12)
        frame_layout.setSpacing(6)

        # Шапка: «Общий прогресс» ... «Файл 2 / 5»
        head_row = QHBoxLayout()
        lbl_overall = QLabel("Общий прогресс")
        lbl_overall.setStyleSheet("color: #c8c8cc; font-size: 11pt; font-weight: bold;")
        head_row.addWidget(lbl_overall)
        head_row.addStretch()
        self.lbl_file_counter = QLabel("")
        self.lbl_file_counter.setStyleSheet(f"color: {self._ACCENT}; font-size: 11pt; font-weight: bold;")
        head_row.addWidget(self.lbl_file_counter)
        frame_layout.addLayout(head_row)

        # Общий прогресс-бар
        self.progress_bar_total = self._make_progress_bar(height=22, font_pt=10)
        frame_layout.addWidget(self.progress_bar_total)

        # Строка деталей (этап слева, имя файла справа) — показывается только во время обработки,
        # чтобы в простое не было пустого «провала» между полосами.
        self.detail_row = QWidget()
        detail_layout = QHBoxLayout(self.detail_row)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(10)
        self.lbl_stage = QLabel("")
        self.lbl_stage.setStyleSheet("color: #d8d8dc; font-size: 9pt; font-weight: 600;")
        detail_layout.addWidget(self.lbl_stage)
        detail_layout.addStretch()
        self.lbl_current_file = QLabel("")
        self.lbl_current_file.setStyleSheet("color: #9a9aa0; font-size: 9pt;")
        detail_layout.addWidget(self.lbl_current_file)
        frame_layout.addWidget(self.detail_row)
        self.detail_row.setVisible(False)  # скрыто в простое

        # Прогресс текущего файла (тоньше)
        self.progress_bar_file = self._make_progress_bar(height=16, font_pt=8)
        frame_layout.addWidget(self.progress_bar_file)

        # Статус / оценка оставшегося времени
        self.lbl_status = QLabel("Готов к работе")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setFixedHeight(28)
        self.lbl_status.setStyleSheet(
            "color: #e0e0e0; font-size: 10pt; font-weight: bold;"
            "background-color: #2a2a2e; border-radius: 6px; padding: 2px;"
        )
        frame_layout.addWidget(self.lbl_status)

        parent_layout.addWidget(progress_frame)

    def log(self, message: str):
        """Добавляет сообщение в лог"""
        self.signals.log_message.emit(message)
        self.app_logger.get_logger().info(message)

    def _append_log(self, message: str):
        """Добавляет сообщение в текстовое поле лога (вызывается из главного потока)"""
        self.log_text.append(f">> {message}")

    def _select_files(self):
        """Обработчик выбора файлов"""
        initial_dir = self.user_settings.get_last_files_dir() or self.input_dir or os.path.expanduser("~")

        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Выберите аудио или видео файлы",
            initial_dir,
            "Медиа файлы (*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.mp4 *.avi *.mov *.mkv *.webm *.wma *.qta *.3gp);;Все файлы (*.*)"
        )

        if files:
            self._apply_dropped_or_selected_files(files)

    def _apply_dropped_or_selected_files(self, files: list, append: bool = False, remember_dir: bool = True):
        """Применяет список выбранных/перетащенных файлов: обновляет очередь, метки и лог"""
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
        self.lbl_files_count.setStyleSheet("color: #dcdcdc;")
        self.log(f"Добавлено в очередь: {len(unique_files)} файлов")
        for f in unique_files:
            self.log(f" + {os.path.basename(f)}")

    def dragEnterEvent(self, event: QDragEnterEvent):
        """Принимаем перетаскивание, если это ссылки на файлы"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        """Обработка сброса файлов/папок: фильтруем по расширению и добавляем в очередь"""
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
                # Рекурсивно сканируем папку
                for root, _dirs, filenames in os.walk(path):
                    for f in filenames:
                        if f.lower().endswith(MEDIA_EXTENSIONS):
                            files.append(os.path.join(root, f))
            elif os.path.isfile(path) and path.lower().endswith(MEDIA_EXTENSIONS):
                files.append(path)
        if files:
            self._apply_dropped_or_selected_files(files)
        elif urls:
            # Были сброшены объекты, но ни один не подошёл по формату
            QMessageBox.information(
                self,
                "Неподдерживаемый формат",
                "Сброшенные файлы не являются поддерживаемыми медиа "
                "(mp3, wav, m4a, aac, flac, ogg, mp4, avi, mov, mkv, webm, wma, qta, 3gp)."
            )
        event.acceptProposedAction()

    def _select_files_folder(self):
        """Обработчик выбора папки с файлами"""
        initial_dir = self.user_settings.get_last_files_dir() or self.input_dir or os.path.expanduser("~")

        folder = QFileDialog.getExistingDirectory(
            self,
            "Выберите папку с аудио/видео файлами",
            initial_dir
        )

        if folder:
            self.input_dir = folder
            self.user_settings.set_last_files_dir(folder)
            self._update_input_dir_label(folder)

            # Рекурсивно собираем файлы из папки и всех подпапок
            files = []
            for root, _dirs, filenames in os.walk(folder):
                for f in filenames:
                    if f.lower().endswith(MEDIA_EXTENSIONS):
                        files.append(os.path.join(root, f))

            if files:
                self.files_to_process = files
                count = len(files)
                self.lbl_files_count.setText(f"Выбрано файлов: {count}")
                self.lbl_files_count.setStyleSheet("color: #dcdcdc;")

                self.log(f"Добавлено из папки (включая подпапки): {count} файлов")
                for f in files:
                    # Показываем относительный путь для читаемости
                    rel = os.path.relpath(f, folder)
                    self.log(f" + {rel}")
            else:
                QMessageBox.information(self, "Информация", "В выбранной папке и подпапках нет поддерживаемых файлов")

    def _select_output_folder(self):
        """Обработчик выбора папки сохранения"""
        initial_dir = self.user_settings.get_last_output_dir() or self.output_dir or os.path.expanduser("~")

        folder = QFileDialog.getExistingDirectory(
            self,
            "Выберите папку для сохранения результатов",
            initial_dir
        )

        if folder:
            self.output_dir = folder
            self.user_settings.set_last_output_dir(folder)
            self._update_output_dir_label(folder)
            self.log(f"Папка для сохранения: {folder}")

    def _update_input_dir_label(self, path: str):
        """Обновляет метку входной папки"""
        display_path = path if len(path) < 60 else f"...{path[-60:]}"
        self.lbl_input_folder.setText(display_path)
        self.lbl_input_folder.setStyleSheet("color: #dcdcdc;")

    def _update_output_dir_label(self, path: str):
        """Обновляет метку выходной папки"""
        display_path = path if len(path) < 60 else f"...{path[-60:]}"
        self.lbl_output_folder.setText(display_path)
        self.lbl_output_folder.setStyleSheet("color: #dcdcdc;")

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
            download_dir = QFileDialog.getExistingDirectory(
                self,
                "Выберите папку для загрузки медиа",
                initial_dir
            )
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

        thread = threading.Thread(
            target=self._download_media,
            args=(url, download_dir),
            daemon=True
        )
        thread.start()

    def _download_media(self, url: str, download_dir: str):
        try:
            result = self.media_downloader.download(
                url,
                download_dir,
                progress_callback=self.signals.download_progress.emit,
                allow_playlist=False,
            )
            files = [
                path for path in result.files
                if os.path.isfile(path) and os.path.getsize(path) > 0
            ]
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

    def _show_hf_token_dialog(self) -> bool:
        """Показывает диалог для ввода HuggingFace токена. Возвращает True если токен сохранён."""
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

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
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
        """Обработчик изменения состояния диаризации"""
        enabling = (state == Qt.CheckState.Checked.value)

        if enabling and not os.getenv("HF_TOKEN", "").startswith("hf_"):
            if not self._show_hf_token_dialog():
                self.cb_diarization.blockSignals(True)
                self.cb_diarization.setChecked(False)
                self.cb_diarization.blockSignals(False)
                return

        self.enable_diarization = enabling
        self.entry_num_speakers.setEnabled(self.enable_diarization)

        # Активируем/деактивируем чекбоксы диаризованных форматов
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
        """Обработчик изменения формата вывода"""
        self.output_formats[fmt] = self.format_checkboxes[fmt].isChecked()

        # Проверяем, что хотя бы один формат выбран
        if not any(self.output_formats.values()):
            self.output_formats['txt'] = True
            self.format_checkboxes['txt'].setChecked(True)
            self.log("ПРЕДУПРЕЖДЕНИЕ: Выбран хотя бы один формат по умолчанию (txt)")

    def _get_selected_formats(self) -> list:
        """Возвращает список выбранных форматов"""
        return [fmt for fmt, enabled in self.output_formats.items() if enabled]

    def _clear_all(self):
        """Очищает все настройки"""
        if self.is_processing:
            reply = QMessageBox.question(
                self,
                "Внимание",
                "Идет обработка файлов. Вы уверены, что хотите сбросить все настройки?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.No:
                return
            # Запрашиваем отмену; worker использует локальный снимок, поэтому
            # сброс self-состояния ниже безопасен и не уронит поток.
            self._cancel_requested = True
            self.is_processing = False
            self.progress_timer.stop()
            self._set_processing_controls_enabled(True)

        # Очищаем данные
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

        # Очищаем интерфейс
        self.log_text.clear()
        self.progress_bar_total.setValue(0)
        self.progress_bar_file.setValue(0)
        self.progress_upload.setValue(0)
        self.lbl_current_file.setText("")
        self.lbl_stage.setText("")
        self.lbl_file_counter.setText("")
        self.detail_row.setVisible(False)
        self.lbl_status.setText("Готов к работе")

        self.lbl_files_count.setText("Файлы не выбраны")
        self.lbl_files_count.setStyleSheet("color: #909090;")
        self.lbl_input_folder.setText("Папка не выбрана")
        self.lbl_input_folder.setStyleSheet("color: #909090;")
        self.lbl_output_folder.setText("Папка не выбрана (по умолчанию - рядом с файлом)")
        self.lbl_output_folder.setStyleSheet("color: #909090;")
        self.input_path.clear()

        self.btn_start.setEnabled(True)
        self.btn_upload.setEnabled(True)
        self.log("Все настройки сброшены")

    def _start_processing_thread(self):
        """Запускает обработку в отдельном потоке"""
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

        # Если папка не выбрана — результаты сохраняются рядом с каждым исходным файлом
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

        # Анализ файлов
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

        # Оценка времени
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

        # Запускаем таймер обновления прогресса (плавная интерполяция)
        self.progress_timer.start(500)

        # Считываем ВСЕ параметры из виджетов в main thread и передаём в поток как
        # снимок (Qt thread-safety): worker не должен читать/писать виджеты и общее
        # изменяемое состояние напрямую.
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

        # Блокируем управляющие виджеты на время обработки
        self._set_processing_controls_enabled(False)

        # Запускаем обработку в отдельном потоке
        thread = threading.Thread(
            target=self._process_files,
            kwargs={"snapshot": snapshot},
            daemon=True
        )
        thread.start()

    def _set_processing_controls_enabled(self, enabled: bool):
        """Включает/выключает виджеты, которые нельзя менять во время обработки."""
        self.cb_diarization.setEnabled(enabled)
        self.entry_num_speakers.setEnabled(enabled and self.enable_diarization)
        self.btn_upload.setEnabled(enabled)
        for cb in self.format_checkboxes.values():
            cb.setEnabled(enabled)
        # Диаризованные форматы доступны только при включённой диаризации
        if enabled:
            for fmt in ('txt_diarize', 'txt_diarize_timecodes'):
                cb = self.format_checkboxes.get(fmt)
                if cb:
                    cb.setEnabled(self.enable_diarization)

    def _process_files(self, snapshot: dict):
        """Основной процесс обработки файлов (выполняется в отдельном потоке).

        Все нужные параметры передаются снимком (snapshot), сформированным в main
        thread, поэтому worker не читает виджеты и не зависит от изменяемого self-состояния
        (которое может быть сброшено через «Очистить» во время обработки).
        """
        num_speakers = snapshot["num_speakers"]
        enable_diarization = snapshot["enable_diarization"]
        selected_formats = snapshot["selected_formats"]
        output_dir = snapshot["output_dir"]
        files = snapshot["files"]
        start_time = snapshot["start_time"]
        total_files = len(files)
        try:
            # Загружаем модель
            if not self.model_loader.load_model(self.log):
                self.signals.processing_finished.emit(False, "Не удалось загрузить модель")
                return

            # Создаем процессор
            processor = TranscriptionProcessor(
                self.model_loader,
                self.stats,
                self.log,
                progress_callback=self._on_file_progress
            )

            # Логируем параметры диаризации
            if enable_diarization:
                if num_speakers is not None:
                    self.log(f"Количество спикеров: {num_speakers}")
                else:
                    self.log("Количество спикеров: автоопределение")

            # Обрабатываем файлы
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

                    # Если глобальная папка не выбрана — сохраняем рядом с исходным файлом
                    file_output_dir = output_dir if output_dir else os.path.dirname(filepath)

                    result = processor.process_file(
                        filepath,
                        file_output_dir,
                        i,
                        total_files,
                        enable_diarization=enable_diarization,
                        num_speakers=num_speakers,
                        output_formats=selected_formats
                    )

                    # Сохраняем статистику
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
                    # Обновляем счётчики для индикатора прогресса (только чтение в таймере)
                    self.files_processed = files_processed
                    self.time_spent = time_spent

            # Завершение
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
            # success=False, если ни один файл не обработан или были сбои/отмена
            success = (files_processed > 0) and (files_failed == 0) and not cancelled
            self.signals.processing_finished.emit(success, message)

        except Exception as e:
            self.log(f"Критическая ошибка: {str(e)}")
            self.signals.processing_finished.emit(False, f"Ошибка: {str(e)}")

    # Человекочитаемые названия этапов
    _STAGE_NAMES = {
        'conversion': 'Конвертация…',
        'transcription': 'Распознавание речи…',
    }

    def _on_file_progress(self, stage: str, progress: float):
        """Callback процессора (вызывается из worker-потока) — только шлём сигнал."""
        self.signals.stage_update.emit(stage, progress)

    def _on_stage_update(self, stage: str, progress: float):
        """Обработчик смены этапа (главный поток): фиксируем начало этапа для интерполяции."""
        if stage != self.current_stage:
            self.current_stage = stage
            self._stage_start_time = time.time()
        self.current_stage_progress = progress
        self._refresh_progress()

    def _estimate_file_progress(self) -> float:
        """Оценка прогресса текущего файла (0..1): этап задаёт диапазон, время — заполнение."""
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
        # Этап ещё не сообщён — грубая оценка по общему времени файла
        elapsed = time.time() - self.current_file_start_time
        return min(0.99, elapsed / total_est)

    def _refresh_progress(self):
        """Единый пересчёт и отрисовка прогресса (вызывается из таймера и при смене этапа)."""
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

        # Оценка оставшегося времени
        remaining_files = self.total_files - files_done
        if remaining_files > 0:
            if files_done > 0 and self.time_spent > 0:
                avg_time = self.time_spent / files_done
            else:
                avg_time = self.file_estimates.get(self.files_to_process[0], 30)
            remaining_time = avg_time * remaining_files
            self.lbl_status.setText(f"Осталось: ~{self.time_formatter.format_duration(remaining_time)}")

    def _update_progress_display(self):
        """Тик таймера — плавная интерполяция прогресса по времени."""
        self._refresh_progress()

    def _update_total_progress(self, value: int):
        """Обновляет общий прогресс-бар"""
        self.progress_bar_total.setValue(value)

    def _update_file_progress(self, value: int):
        """Обновляет прогресс-бар текущего файла"""
        self.progress_bar_file.setValue(value)

    def _update_current_file_info(self, info: str):
        """Обновляет имя текущего файла (этап и счётчик отображаются отдельно)."""
        # Сброс этапа на старте нового файла, чтобы не показывать прогресс предыдущего
        self.current_stage = None
        self.current_stage_progress = 0.0
        display = info if len(info) <= 64 else f"…{info[-64:]}"
        self._current_filename = display
        self.lbl_current_file.setText(display)

    def _on_processing_finished(self, success: bool, message: str):
        """Обработчик завершения обработки"""
        self.is_processing = False
        self.progress_timer.stop()

        self.btn_start.setEnabled(True)
        self.btn_start.setText("ЗАПУСТИТЬ ОБРАБОТКУ")
        self.progress_bar_total.setValue(100 if success else self.progress_bar_total.value())
        self.progress_bar_file.setValue(100 if success else self.progress_bar_file.value())
        self.lbl_stage.setText("✓  Готово" if success else "✕  Остановлено")
        self.lbl_status.setText(message)

        # Возвращаем управляющие виджеты в активное состояние
        self._set_processing_controls_enabled(True)

        if success:
            QMessageBox.information(self, "Готово", f"Обработка завершена!\n{message}")
        else:
            QMessageBox.warning(self, "Завершено", message)

    def closeEvent(self, event):
        """Обработчик закрытия окна"""
        # Если идёт обработка или загрузка — подтверждаем и запрашиваем отмену
        if self.is_processing or self.is_downloading:
            reply = QMessageBox.question(
                self,
                "Внимание",
                "Идёт обработка/загрузка. Закрыть приложение и прервать её?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return
            # Сообщаем worker-потоку остановиться после текущего файла
            self._cancel_requested = True
            self.is_processing = False

        if self.output_dir:
            self.user_settings.set_last_output_dir(self.output_dir)
        if self.input_dir:
            self.user_settings.set_last_files_dir(self.input_dir)

        self.app_logger.log_session_end()
        event.accept()


def run_qt_app():
    """Запускает приложение на PyQt6"""
    # AppUserModelID до QApplication — иначе Windows показывает иконку терминала
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(u'GigaAM.Transcriber.v3')
        except Exception:
            pass

    app = QApplication(sys.argv)

    font = QFont("Arial", 12)
    app.setFont(font)

    # Иконка приложения
    icon_path = os.path.join(
        getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
        'icon.ico'
    )
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    window = GigaTranscriberQtApp()
    window.show()

    # Win32: явно ставим иконку на окно и в taskbar через WM_SETICON
    if sys.platform == 'win32' and os.path.exists(icon_path):
        def _set_win32_icon():
            try:
                import ctypes
                hwnd = int(window.winId())
                hicon = ctypes.windll.user32.LoadImageW(
                    None, icon_path, 1, 0, 0, 0x10 | 0x40
                )
                if hicon:
                    ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 0, hicon)
                    ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 1, hicon)
            except Exception:
                pass
        QTimer.singleShot(100, _set_win32_icon)

    sys.exit(app.exec())


if __name__ == "__main__":
    run_qt_app()
