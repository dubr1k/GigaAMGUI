"""Конструирование интерфейса GigaTranscriberQtApp (окно, меню, группы виджетов).

Mixin: методы _init_ui/_build_menu_bar/_create_*_group работают со `self` окна.
Поведение сохранено 1:1.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QFont, QKeySequence
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..config import APP_TITLE, OUTPUT_FORMATS


class UiBuildMixin:
    def _init_ui(self):
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(self._px(940), self._px(680))
        self.resize(self._px(1040), self._px(1000))

        self._build_menu_bar()

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(self._px(16), self._px(12), self._px(16), self._px(12))
        root_layout.setSpacing(self._px(8))
        self.setCentralWidget(root)

        # Заголовок + кнопка темы
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)

        self._header_left_spacer = QWidget()
        header_btn_width = self._px(48) + self._px(42) + header_row.spacing()
        self._header_left_spacer.setFixedWidth(header_btn_width)
        header_row.addWidget(self._header_left_spacer)

        self._title_label = QLabel("GigaAM v3: Транскрибация")
        self._title_label.setFont(self._font(18, QFont.Weight.Bold))
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_label.setFixedHeight(self._px(40))
        header_row.addWidget(self._title_label, 1)

        self._btn_lang = QPushButton("EN" if self._lang == "ru" else "RU")
        self._btn_lang.setObjectName("theme_button")
        self._btn_lang.setFixedSize(self._px(48), self._px(36))
        self._btn_lang.setToolTip("Switch language")
        self._btn_lang.clicked.connect(self._toggle_language)
        header_row.addWidget(self._btn_lang)

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
        main_layout.setContentsMargins(self._px(8), self._px(14), self._px(8), self._px(6))
        main_layout.setSpacing(self._px(4))

        main_layout.addWidget(self._create_files_group())
        output_options_row = QHBoxLayout()
        output_options_row.setSpacing(self._px(6))
        output_options_row.addWidget(self._create_output_group(), 3)
        output_options_row.addWidget(self._create_audio_preprocessing_group(), 2)
        main_layout.addLayout(output_options_row)
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

        llm_scroll = QScrollArea()
        llm_scroll.setWidgetResizable(True)
        llm_scroll.setFrameShape(QFrame.Shape.NoFrame)
        llm_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        llm_scroll.setWidget(self._create_llm_tab())
        tabs.addTab(llm_scroll, "LLM")

        # ── Вкладка «Журнал» ──
        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        log_layout.setContentsMargins(self._px(8), self._px(14), self._px(8), self._px(8))
        log_layout.setSpacing(self._px(6))

        log_toolbar = QHBoxLayout()
        log_toolbar.setSpacing(self._px(8))
        self.btn_log_copy = QPushButton("Копировать")
        self.btn_log_copy.setToolTip("Скопировать весь журнал в буфер обмена")
        self.btn_log_copy.setFixedHeight(self._px(32))
        self.btn_log_copy.clicked.connect(self._copy_log)
        log_toolbar.addWidget(self.btn_log_copy)
        self.btn_log_save = QPushButton("Сохранить…")
        self.btn_log_save.setToolTip("Сохранить журнал в текстовый файл")
        self.btn_log_save.setFixedHeight(self._px(32))
        self.btn_log_save.clicked.connect(self._save_log)
        log_toolbar.addWidget(self.btn_log_save)
        self.btn_log_clear = QPushButton("Очистить журнал")
        self.btn_log_clear.setToolTip("Очистить только журнал, не сбрасывая настройки")
        self.btn_log_clear.setFixedHeight(self._px(32))
        self.btn_log_clear.clicked.connect(self._clear_log)
        log_toolbar.addWidget(self.btn_log_clear)
        log_toolbar.addStretch()
        log_layout.addLayout(log_toolbar)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(self._font(11, fixed=True))
        self.log_text.setMinimumHeight(self._px(160))
        log_layout.addWidget(self.log_text, 1)
        tabs.addTab(log_tab, "Журнал обработки")
        self.tabs = tabs
        self._apply_language()

        # Статус-бар: краткие подсказки и состояние
        self.status_bar = self.statusBar()
        self.status_bar.showMessage(self._t("Готов к работе", "Ready to work"))

        self._ensure_llm_settings_dialog()
        self._apply_language()

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

        self._menu_file = menubar.addMenu("Файл")
        file_menu = self._menu_file
        self._act_files = QAction("Выбрать файлы…", self)
        act_files = self._act_files
        act_files.setShortcut(QKeySequence.StandardKey.Open)
        act_files.setStatusTip("Добавить аудио- или видеофайлы в очередь")
        act_files.triggered.connect(self._select_files)
        file_menu.addAction(act_files)

        self._act_folder = QAction("Выбрать папку с файлами…", self)
        act_folder = self._act_folder
        act_folder.setStatusTip("Добавить все медиафайлы из папки и подпапок")
        act_folder.triggered.connect(self._select_files_folder)
        file_menu.addAction(act_folder)

        self._act_out = QAction("Папка сохранения…", self)
        act_out = self._act_out
        act_out.setStatusTip("Выбрать папку для результатов транскрибации")
        act_out.triggered.connect(self._select_output_folder)
        file_menu.addAction(act_out)

        file_menu.addSeparator()
        self._act_open_res = QAction("Открыть папку с результатами", self)
        act_open_res = self._act_open_res
        act_open_res.setStatusTip("Открыть папку с готовыми файлами")
        act_open_res.triggered.connect(self._open_results_folder)
        file_menu.addAction(act_open_res)

        file_menu.addSeparator()
        self._act_quit = QAction("Выход", self)
        act_quit = self._act_quit
        act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        self._menu_view = menubar.addMenu("Вид")
        view_menu = self._menu_view
        self._act_theme = QAction("Переключить тему", self)
        self._act_theme.setShortcut(QKeySequence("Ctrl+T"))
        self._act_theme.setStatusTip("Светлая / тёмная тема оформления")
        self._act_theme.triggered.connect(self._toggle_theme)
        view_menu.addAction(self._act_theme)

        self._act_accent = QAction("Акцентный цвет…", self)
        self._act_accent.setStatusTip("Выбрать акцентный цвет интерфейса")
        self._act_accent.triggered.connect(self._choose_accent_color)
        view_menu.addAction(self._act_accent)

        self._act_accent_reset = QAction("Сбросить акцентный цвет", self)
        self._act_accent_reset.setStatusTip("Вернуть стандартный акцентный цвет")
        self._act_accent_reset.triggered.connect(self._reset_accent_color)
        view_menu.addAction(self._act_accent_reset)

        self._menu_settings = menubar.addMenu("Настройки")
        settings_menu = self._menu_settings
        self._act_asr_model = QAction("Модель распознавания…", self)
        self._act_asr_model.setStatusTip("Выбрать модель GigaAM для следующей обработки")
        self._act_asr_model.triggered.connect(self._select_asr_model)
        settings_menu.addAction(self._act_asr_model)

        self._act_asr_backend = QAction("Движок распознавания…", self)
        act_asr_backend = self._act_asr_backend
        act_asr_backend.setStatusTip("Выбрать backend для распознавания речи")
        act_asr_backend.triggered.connect(self._select_asr_backend)
        settings_menu.addAction(act_asr_backend)

        settings_menu.addSeparator()
        self._act_device = QAction("Устройство (CPU / GPU)…", self)
        act_device = self._act_device
        act_device.setStatusTip("Выбрать CPU или видеокарту NVIDIA для распознавания")
        act_device.triggered.connect(self._change_device)
        settings_menu.addAction(act_device)

        settings_menu.addSeparator()
        self._act_data_dir = QAction("Папка данных и моделей…", self)
        self._act_data_dir.setStatusTip("Выбрать диск для моделей, кэшей и runtime")
        self._act_data_dir.triggered.connect(self._select_data_directory)
        settings_menu.addAction(self._act_data_dir)

        settings_menu.addSeparator()
        self._act_llm = QAction("LLM API…", self)
        act_llm = self._act_llm
        act_llm.setStatusTip("Настроить API URL, ключ, модель и папку результатов LLM")
        act_llm.triggered.connect(self._open_llm_settings_dialog)
        settings_menu.addAction(act_llm)

        self._menu_help = menubar.addMenu("Справка")
        help_menu = self._menu_help
        self._act_about = QAction("О программе", self)
        act_about = self._act_about
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

    def _show_about(self):
        diag = self.model_loader.diagnostics() if self.model_loader is not None else {}
        diag_lines = [
            f"requested_backend={diag.get('requested_backend')}",
            f"active_backend={diag.get('active_backend')}",
            f"model={diag.get('model')}",
            f"device={diag.get('device')}",
            f"repo={diag.get('repo')}",
            f"fallback_reason={diag.get('fallback_reason')}",
            f"cache_root={diag.get('cache_root')}",
        ]
        diagnostics = "<br>".join(diag_lines)
        QMessageBox.about(
            self,
            self._t("О программе", "About"),
            (
                f"<b>{APP_TITLE}</b><br><br>"
                "Локальная транскрибация аудио и видео на модели <b>GigaAM v3</b> с поддержкой диаризации спикеров.<br><br>"
                "Возможности: пакетная обработка, загрузка по ссылке, таймкоды, экспорт в TXT / Markdown / SRT / VTT.<br><br>"
                f"Диагностика ASR:<br>{diagnostics}<br><br>"
                "Поддерживаемые форматы ввода: mp3, wav, m4a, aac, flac, ogg, mp4, avi, mov, mkv, webm, wma, 3gp."
            ) if self._lang == "ru" else (
                f"<b>{APP_TITLE}</b><br><br>"
                "Local audio and video transcription powered by <b>GigaAM v3</b> with speaker diarization support.<br><br>"
                "Features: batch processing, URL download, timecodes, export to TXT / Markdown / SRT / VTT.<br><br>"
                f"ASR diagnostics:<br>{diagnostics}<br><br>"
                "Supported input formats: mp3, wav, m4a, aac, flac, ogg, mp4, avi, mov, mkv, webm, wma, 3gp."
            )
        )

    _ACCENT_LIGHT = "#3b82f6"
    _CONVERSION_BAND = 0.15

    def _make_progress_bar(self, height: int, font_pt: int) -> QProgressBar:
        c = self._colors()
        bar = QProgressBar()
        scaled_height = self._px(height)
        # На macOS шкала скругляется в «пилюлю», только когда border-radius РОВНО
        # равен половине высоты. При нечётной высоте radius = height // 2 < height/2,
        # и нативный стиль рисует прямоугольник. Поэтому высоту делаем чётной.
        if scaled_height % 2:
            scaled_height += 1
        bar.setFixedHeight(scaled_height)
        bar.setTextVisible(True)
        bar.setRange(0, 100)
        radius = scaled_height // 2
        r, r2 = c["progress_chunk"], c["progress_chunk2"]
        bar.setStyleSheet(
            f"QProgressBar {{ border: none; border-radius: {radius}px;"
            f"  background-color: {c['progress_bg']}; text-align: center; color: {c['text']};"
            f"  font-size: {self._pt_css(font_pt)}pt; font-weight: 600; }}"
            # Градиент вертикальный (x2=0), а не по ширине заполнения: цвет шкалы
            # больше не «плывёт» по мере заполнения от 0 до 100%.
            f"QProgressBar::chunk {{ border-radius: {radius}px;"
            f"  background-color: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
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
        self.lbl_overall = QLabel("Общий прогресс")
        lbl_overall = self.lbl_overall
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
        # Контейнер-QWidget иначе красится глобальным правилом QWidget в цвет фона
        # окна (темнее карточки) и выглядит как чёрная «вдавленная» рамка.
        self.detail_row.setStyleSheet("background: transparent;")
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

        self.lbl_status = QLabel(self._t("Готов к работе", "Ready to work"))
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setFixedHeight(self._px(28))
        # Фон прозрачный, чтобы строка статуса сливалась с карточкой, а не
        # выглядела как тёмная «вдавленная» рамка (status_bg темнее карточки).
        self.lbl_status.setStyleSheet(
            f"color: {c['status_text']}; font-size: {self._pt_css(10)}pt; font-weight: bold;"
            f"background: transparent; padding: {self._px(2)}px;"
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
        self.grp_files = group
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(self._px(12), self._px(8), self._px(12), self._px(8))
        main_layout.setSpacing(self._px(6))

        row1 = QHBoxLayout()
        row1.setSpacing(self._px(10))
        self.btn_select_files = QPushButton("Выбрать файлы")
        btn_select_files = self.btn_select_files
        btn_select_files.setToolTip("Выбрать аудио/видео файлы для обработки  (Ctrl+O)")
        btn_select_files.clicked.connect(self._select_files)
        btn_select_files.setFixedHeight(self._px(36))
        btn_select_files.setMinimumWidth(self._px(160))
        row1.addWidget(btn_select_files)

        self.btn_select_folder = QPushButton("Выбрать папку")
        btn_select_folder = self.btn_select_folder
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

    def _size_list_to_contents(self, list_widget: QListWidget, min_rows: int = 1, max_rows: int = 5):
        """Выставляет высоту списка по числу строк, вместо фиксированного
        большого блока с пустым местом для 1-2 файлов."""
        row_h = list_widget.sizeHintForRow(0)
        if row_h <= 0:
            row_h = self._px(24)
        count = max(min_rows, min(max_rows, list_widget.count() or min_rows))
        frame = self._px(2) * 2 + self._px(4)
        list_widget.setFixedHeight(row_h * count + frame)

    def _style_drop_hint(self):
        c = self._colors()
        active = getattr(self, "_drop_active", False)
        border = c["accent"] if active else c["border"]
        bg = c["btn_hover_bg"] if active else c["bg_card"]
        text = c["accent"] if active else c["text_mute2"]
        if hasattr(self, "drop_hint"):
            self.drop_hint.setStyleSheet(
                f"#drop_hint {{ border: 2px dashed {border}; border-radius: {self._px(10)}px;"
                f"  background-color: {bg}; color: {text};"
                f"  font-size: {self._pt_css(11)}pt; padding: {self._px(8)}px; }}"
            )
        if hasattr(self, "llm_drop_hint"):
            self.llm_drop_hint.setStyleSheet(
                f"#llm_drop_hint {{ border: 2px dashed {border}; border-radius: {self._px(10)}px;"
                f"  background-color: {bg}; color: {text};"
                f"  font-size: {self._pt_css(11)}pt; padding: {self._px(8)}px; }}"
            )

    def _create_output_group(self) -> QGroupBox:
        group = QGroupBox("2. Папка сохранения результатов")
        self.grp_output = group
        layout = QHBoxLayout()
        layout.setContentsMargins(self._px(12), self._px(8), self._px(12), self._px(10))
        layout.setSpacing(self._px(12))
        self.btn_output_select = QPushButton("Выбрать папку")
        btn_output = self.btn_output_select
        btn_output.clicked.connect(self._select_output_folder)
        btn_output.setMinimumWidth(self._px(220))
        btn_output.setFixedHeight(self._px(36))
        layout.addWidget(btn_output)
        self.lbl_output_folder = QLabel("Папка не выбрана (по умолчанию - рядом с файлом)")
        self.lbl_output_folder.setStyleSheet(self._transparent_label_style(self._colors()["text_mute"]))
        layout.addWidget(self.lbl_output_folder, 1)
        group.setLayout(layout)
        return group

    def _create_formats_group(self) -> QGroupBox:
        group = QGroupBox("5. Форматы вывода")
        self.grp_formats = group
        layout = QVBoxLayout()
        layout.setContentsMargins(self._px(12), self._px(8), self._px(12), self._px(10))
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

    # _show_hf_token_dialog вынесен в FilesMixin (рядом с _toggle_diarization).
