"""
Главное окно приложения GigaAM v3 Transcriber на PyQt6
Строгий профессиональный дизайн без ярких цветов
"""

import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QProgressBar, QFileDialog,
    QMessageBox, QCheckBox, QLineEdit, QGroupBox, QFrame
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QPalette, QColor, QDragEnterEvent, QDropEvent

from ..config import APP_TITLE, SUPPORTED_FORMATS, STATS_FILE, OUTPUT_FORMATS, MEDIA_EXTENSIONS
from ..core import ModelLoader, TranscriptionProcessor
from ..utils import ProcessingStats, TimeFormatter, AudioConverter, AppLogger, UserSettings


class WorkerSignals(QObject):
    """Сигналы для потока обработки"""
    log_message = pyqtSignal(str)
    progress_update = pyqtSignal(int)
    file_progress_update = pyqtSignal(int)
    current_file_info = pyqtSignal(str)
    processing_finished = pyqtSignal(bool, str)
    stage_update = pyqtSignal(str, float)


class GigaTranscriberQtApp(QMainWindow):
    """Главное окно приложения для транскрибации на PyQt6"""

    def __init__(self):
        super().__init__()
        
        # Переменные состояния
        self.files_to_process = []
        self.output_dir = ""
        self.input_dir = ""
        self.is_processing = False
        self.start_time = None
        self.files_processed = 0
        self.total_files = 0
        self.file_estimates = {}
        self.total_estimated_time = 0
        self.time_spent = 0
        self.current_file_start_time = 0
        self.current_stage = None
        self.current_stage_progress = 0.0
        
        # Настройки диаризации
        self.enable_diarization = False
        self.num_speakers = None
        
        # Настройки выходных форматов
        self.output_formats = {'txt': True, 'md': False, 'srt': False, 'vtt': False}
        
        # Инициализация модулей
        self.app_logger = AppLogger()
        self.app_logger.log_session_start()
        self.model_loader = ModelLoader()
        self.stats = ProcessingStats(STATS_FILE)
        self.time_formatter = TimeFormatter()
        self.user_settings = UserSettings()
        
        # Сигналы для потока обработки
        self.signals = WorkerSignals()
        self.signals.log_message.connect(self._append_log)
        self.signals.progress_update.connect(self._update_total_progress)
        self.signals.file_progress_update.connect(self._update_file_progress)
        self.signals.current_file_info.connect(self._update_current_file_info)
        self.signals.processing_finished.connect(self._on_processing_finished)
        self.signals.stage_update.connect(self._on_stage_update)
        
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
        # Достаточный минимальный и стартовый размер, чтобы на Linux элементы не наезжали
        self.setMinimumSize(1000, 820)
        self.resize(1280, 960)
        
        # Центральный виджет
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(6)
        
        # Заголовок
        title_label = QLabel("GigaAM v3: Транскрибация")
        title_label.setFont(QFont("Arial", 18, QFont.Weight.Bold))
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setFixedHeight(40)
        main_layout.addWidget(title_label)
        
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
        
        # Лог
        log_label = QLabel("Журнал обработки:")
        log_label.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        log_label.setFixedHeight(24)
        main_layout.addWidget(log_label)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 11))
        self.log_text.setMinimumHeight(160)
        main_layout.addWidget(self.log_text, 1)  # Растягивается
        
        # Кнопка очистки
        self.btn_clear = QPushButton("ОЧИСТИТЬ ВСЕ")
        self.btn_clear.setObjectName("clear_button")
        self.btn_clear.setFixedHeight(40)
        self.btn_clear.clicked.connect(self._clear_all)
        main_layout.addWidget(self.btn_clear)
        
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
        """Создает блок выбора файлов"""
        group = QGroupBox("1. Выбор файлов")
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 20, 12, 10)
        layout.setSpacing(8)
        
        # Первая строка: выбор отдельных файлов
        row1 = QHBoxLayout()
        row1.setSpacing(12)
        btn_select_files = QPushButton("Выбрать файлы")
        btn_select_files.setToolTip("Или перетащите файлы сюда")
        btn_select_files.clicked.connect(self._select_files)
        btn_select_files.setMinimumWidth(220)
        btn_select_files.setFixedHeight(36)
        row1.addWidget(btn_select_files)
        
        self.lbl_files_count = QLabel("Файлы не выбраны")
        self.lbl_files_count.setStyleSheet("color: #909090;")
        row1.addWidget(self.lbl_files_count, 1)
        
        layout.addLayout(row1)
        
        # Вторая строка: выбор папки
        row2 = QHBoxLayout()
        row2.setSpacing(12)
        btn_select_folder = QPushButton("Выбрать папку с файлами")
        btn_select_folder.clicked.connect(self._select_files_folder)
        btn_select_folder.setMinimumWidth(220)
        btn_select_folder.setFixedHeight(36)
        row2.addWidget(btn_select_folder)
        
        self.lbl_input_folder = QLabel("Папка не выбрана")
        self.lbl_input_folder.setStyleSheet("color: #909090;")
        row2.addWidget(self.lbl_input_folder, 1)
        
        layout.addLayout(row2)
        group.setLayout(layout)
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
        layout = QHBoxLayout()
        layout.setContentsMargins(12, 20, 12, 10)
        layout.setSpacing(20)
        
        self.format_checkboxes = {}
        for fmt, label in OUTPUT_FORMATS.items():
            cb = QCheckBox(label)
            cb.setChecked(fmt == 'txt')  # txt по умолчанию
            cb.stateChanged.connect(lambda state, f=fmt: self._toggle_format(f))
            layout.addWidget(cb)
            self.format_checkboxes[fmt] = cb
        
        layout.addStretch()
        group.setLayout(layout)
        return group

    def _create_progress_section(self, parent_layout):
        """Создает секцию прогресса без QGroupBox для избежания проблем с отступами"""
        # Заголовок секции
        lbl_title = QLabel("Прогресс обработки")
        lbl_title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        lbl_title.setFixedHeight(28)
        parent_layout.addWidget(lbl_title)
        
        # Контейнер прогресса
        progress_frame = QFrame()
        progress_frame.setStyleSheet(
            "QFrame { background-color: #38383b; border: 1px solid #4a4a4e; border-radius: 6px; }"
        )
        frame_layout = QVBoxLayout(progress_frame)
        frame_layout.setContentsMargins(14, 14, 14, 14)
        frame_layout.setSpacing(10)
        
        # Общий прогресс
        total_layout = QHBoxLayout()
        total_layout.setSpacing(10)
        lbl_total = QLabel("Всего:")
        lbl_total.setFixedWidth(60)
        lbl_total.setStyleSheet("border: none;")
        total_layout.addWidget(lbl_total)
        
        self.progress_bar_total = QProgressBar()
        self.progress_bar_total.setFixedHeight(24)
        self.progress_bar_total.setTextVisible(True)
        self.progress_bar_total.setStyleSheet(
            "QProgressBar { border: 1px solid #555; border-radius: 4px;"
            "  text-align: center; background-color: #252528; color: #e0e0e0; font-size: 10pt; }"
            "QProgressBar::chunk { background-color: #5a5a5d; border-radius: 3px; }"
        )
        total_layout.addWidget(self.progress_bar_total, 1)
        frame_layout.addLayout(total_layout)
        
        # Прогресс текущего файла
        file_layout = QHBoxLayout()
        file_layout.setSpacing(10)
        lbl_file = QLabel("Файл:")
        lbl_file.setFixedWidth(60)
        lbl_file.setStyleSheet("border: none;")
        file_layout.addWidget(lbl_file)
        
        self.progress_bar_file = QProgressBar()
        self.progress_bar_file.setFixedHeight(20)
        self.progress_bar_file.setTextVisible(True)
        self.progress_bar_file.setStyleSheet(
            "QProgressBar { border: 1px solid #555; border-radius: 4px;"
            "  text-align: center; background-color: #252528; color: #e0e0e0; font-size: 9pt; }"
            "QProgressBar::chunk { background-color: #5a5a5d; border-radius: 3px; }"
        )
        file_layout.addWidget(self.progress_bar_file, 1)
        frame_layout.addLayout(file_layout)
        
        # Информация о текущем файле
        self.lbl_current_file = QLabel(" ")
        self.lbl_current_file.setStyleSheet("color: #b0b0b0; font-size: 9pt; border: none;")
        self.lbl_current_file.setFixedHeight(20)
        frame_layout.addWidget(self.lbl_current_file)
        
        # Статус
        self.lbl_status = QLabel("Готов к работе")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setFixedHeight(30)
        self.lbl_status.setStyleSheet(
            "color: #e0e0e0; font-size: 11pt; font-weight: bold;"
            "background-color: #2d2d30; border: 1px solid #4a4a4e; border-radius: 4px; padding: 2px;"
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
            "Медиа файлы (*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.mp4 *.avi *.mov *.mkv *.webm *.wma *.qta);;Все файлы (*.*)"
        )
        
        if files:
            self._apply_dropped_or_selected_files(files)

    def _apply_dropped_or_selected_files(self, files: list):
        """Применяет список выбранных/перетащенных файлов: обновляет очередь, метки и лог"""
        if not files:
            return
        self.files_to_process = files
        count = len(files)
        file_dir = os.path.dirname(files[0])
        self.input_dir = file_dir
        self.user_settings.set_last_files_dir(files[0])
        self.lbl_files_count.setText(f"Выбрано файлов: {count}")
        self.lbl_files_count.setStyleSheet("color: #dcdcdc;")
        self.log(f"Добавлено в очередь: {count} файлов")
        for f in files:
            self.log(f" + {os.path.basename(f)}")

    def dragEnterEvent(self, event: QDragEnterEvent):
        """Принимаем перетаскивание, если это ссылки на файлы"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        """Обработка сброса файлов: фильтруем по расширению и добавляем в очередь"""
        urls = event.mimeData().urls()
        if not urls:
            event.acceptProposedAction()
            return
        files = []
        for url in urls:
            path = url.toLocalFile()
            if not path:
                continue
            if os.path.isfile(path) and path.lower().endswith(MEDIA_EXTENSIONS):
                files.append(path)
        if files:
            self._apply_dropped_or_selected_files(files)
        elif urls:
            # Были сброшены файлы, но ни один не подошёл по формату
            QMessageBox.information(
                self,
                "Неподдерживаемый формат",
                "Сброшенные файлы не являются поддерживаемыми медиа (mp3, wav, m4a, aac, flac, ogg, mp4, avi, mov, mkv, webm, wma, qta)."
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
            
            # Собираем файлы из папки
            files = [
                os.path.join(folder, f)
                for f in os.listdir(folder)
                if os.path.isfile(os.path.join(folder, f)) and f.lower().endswith(MEDIA_EXTENSIONS)
            ]
            
            if files:
                self.files_to_process = files
                count = len(files)
                self.lbl_files_count.setText(f"Выбрано файлов: {count}")
                self.lbl_files_count.setStyleSheet("color: #dcdcdc;")
                
                self.log(f"Добавлено из папки: {count} файлов")
                for f in files:
                    self.log(f" + {os.path.basename(f)}")
            else:
                QMessageBox.information(self, "Информация", "В выбранной папке нет поддерживаемых файлов")

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

    def _toggle_diarization(self, state):
        """Обработчик изменения состояния диаризации"""
        self.enable_diarization = (state == Qt.CheckState.Checked.value)
        self.entry_num_speakers.setEnabled(self.enable_diarization)
        
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
            self.is_processing = False
            self.progress_timer.stop()
        
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
        
        # Очищаем интерфейс
        self.log_text.clear()
        self.progress_bar_total.setValue(0)
        self.progress_bar_file.setValue(0)
        self.lbl_current_file.setText("")
        self.lbl_status.setText("Готов к работе")
        
        self.lbl_files_count.setText("Файлы не выбраны")
        self.lbl_files_count.setStyleSheet("color: #909090;")
        self.lbl_input_folder.setText("Папка не выбрана")
        self.lbl_input_folder.setStyleSheet("color: #909090;")
        self.lbl_output_folder.setText("Папка не выбрана (по умолчанию - рядом с файлом)")
        self.lbl_output_folder.setStyleSheet("color: #909090;")
        
        self.btn_start.setEnabled(True)
        self.log("Все настройки сброшены")

    def _start_processing_thread(self):
        """Запускает обработку в отдельном потоке"""
        if self.is_processing:
            return
        
        if not self.files_to_process:
            QMessageBox.warning(self, "Внимание", "Выберите хотя бы один файл для обработки!")
            return
        
        # Если папка не выбрана, используем директорию первого файла
        if not self.output_dir and self.files_to_process:
            self.output_dir = os.path.dirname(self.files_to_process[0])
            self.user_settings.set_last_output_dir(self.output_dir)
            self.log(f"Папка не выбрана. Использую директорию первого файла: {self.output_dir}")
        
        self.is_processing = True
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
        self.lbl_status.setText(f"Оценка: ~{estimate_str}")
        
        # Запускаем таймер обновления прогресса
        self.progress_timer.start(1000)  # Обновление каждую секунду
        
        # Считываем параметры диаризации из виджета в main thread (Qt thread-safety)
        num_speakers = None
        if self.enable_diarization:
            try:
                speakers_text = self.entry_num_speakers.text().strip()
                if speakers_text:
                    num_speakers = int(speakers_text)
            except ValueError:
                pass
        
        # Запускаем обработку в отдельном потоке
        thread = threading.Thread(
            target=self._process_files,
            args=(num_speakers,),
            daemon=True
        )
        thread.start()

    def _process_files(self, num_speakers: int = None):
        """Основной процесс обработки файлов (выполняется в отдельном потоке)
        
        Args:
            num_speakers: Количество спикеров (считано из виджета в main thread)
        """
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
            if self.enable_diarization:
                if num_speakers is not None:
                    self.log(f"Количество спикеров: {num_speakers}")
                else:
                    self.log("Количество спикеров: автоопределение")
            
            # Обрабатываем файлы
            for i, filepath in enumerate(self.files_to_process):
                if not self.is_processing:
                    break
                
                try:
                    self.current_file_start_time = time.time()
                    self.signals.current_file_info.emit(
                        f"Файл {i + 1}/{self.total_files}: {os.path.basename(filepath)}"
                    )
                    
                    result = processor.process_file(
                        filepath,
                        self.output_dir,
                        i,
                        self.total_files,
                        enable_diarization=self.enable_diarization,
                        num_speakers=num_speakers,
                        output_formats=self._get_selected_formats()
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
                        self.files_processed += 1
                    
                    self.time_spent += result['total_time']
                    
                except Exception as e:
                    self.log(f"Ошибка при обработке файла {os.path.basename(filepath)}: {str(e)}")
                    continue
            
            # Завершение
            total_elapsed = time.time() - self.start_time
            self.log("=== ВСЕ ФАЙЛЫ ОБРАБОТАНЫ ===")
            self.log(f"Общее время обработки: {self.time_formatter.format_duration(total_elapsed)}")
            self.log(f"Обработано файлов: {self.files_processed}/{self.total_files}")
            
            self.signals.processing_finished.emit(
                True,
                f"Завершено за {self.time_formatter.format_duration(total_elapsed)}"
            )
            
        except Exception as e:
            self.log(f"Критическая ошибка: {str(e)}")
            self.signals.processing_finished.emit(False, f"Ошибка: {str(e)}")

    def _on_file_progress(self, stage: str, progress: float):
        """Callback для обновления прогресса файла"""
        self.current_stage = stage
        self.current_stage_progress = progress
        self.signals.stage_update.emit(stage, progress)

    def _on_stage_update(self, stage: str, progress: float):
        """Обработчик обновления этапа"""
        self.current_stage = stage
        self.current_stage_progress = progress

    def _update_progress_display(self):
        """Обновляет отображение прогресса"""
        if not self.is_processing or self.total_files == 0:
            return
        
        # Общий прогресс
        files_progress = self.files_processed / self.total_files
        
        # Прогресс текущего файла
        current_file_progress = 0.0
        if self.files_processed < len(self.files_to_process) and self.current_file_start_time > 0:
            current_filepath = self.files_to_process[self.files_processed]
            current_elapsed = time.time() - self.current_file_start_time
            estimated_time = self.file_estimates.get(current_filepath, 30)
            
            if estimated_time > 0:
                current_file_progress = min(0.95, current_elapsed / estimated_time)
        
        # Общий прогресс
        overall_progress = files_progress + (current_file_progress / self.total_files)
        overall_progress = min(0.99, overall_progress)
        
        # Обновляем прогресс-бары
        self.progress_bar_total.setValue(int(overall_progress * 100))
        self.progress_bar_file.setValue(int(current_file_progress * 100))
        
        # Оставшееся время
        if self.files_processed < len(self.files_to_process):
            remaining_files = len(self.files_to_process) - self.files_processed
            
            if self.files_processed > 0:
                avg_time = self.time_spent / self.files_processed
            else:
                avg_time = self.file_estimates.get(self.files_to_process[0], 30)
            
            remaining_time = avg_time * remaining_files
            time_info = f"Осталось: ~{self.time_formatter.format_duration(remaining_time)}"
            self.lbl_status.setText(time_info)

    def _update_total_progress(self, value: int):
        """Обновляет общий прогресс-бар"""
        self.progress_bar_total.setValue(value)

    def _update_file_progress(self, value: int):
        """Обновляет прогресс-бар текущего файла"""
        self.progress_bar_file.setValue(value)

    def _update_current_file_info(self, info: str):
        """Обновляет информацию о текущем файле"""
        self.lbl_current_file.setText(info)

    def _on_processing_finished(self, success: bool, message: str):
        """Обработчик завершения обработки"""
        self.is_processing = False
        self.progress_timer.stop()
        
        self.btn_start.setEnabled(True)
        self.btn_start.setText("ЗАПУСТИТЬ ОБРАБОТКУ")
        self.progress_bar_total.setValue(100 if success else 0)
        self.lbl_status.setText(message)
        
        if success:
            QMessageBox.information(self, "Готово", f"Обработка завершена!\n{message}")
        else:
            QMessageBox.warning(self, "Ошибка", message)

    def closeEvent(self, event):
        """Обработчик закрытия окна"""
        if self.output_dir:
            self.user_settings.set_last_output_dir(self.output_dir)
        if self.input_dir:
            self.user_settings.set_last_files_dir(self.input_dir)
        
        self.app_logger.log_session_end()
        event.accept()


def run_qt_app():
    """Запускает приложение на PyQt6"""
    app = QApplication(sys.argv)
    
    # Устанавливаем шрифт по умолчанию (крупный размер для удобства чтения)
    font = QFont("Arial", 12)
    app.setFont(font)
    
    window = GigaTranscriberQtApp()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    run_qt_app()
