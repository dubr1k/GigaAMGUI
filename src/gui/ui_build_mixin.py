"""Конструирование интерфейса GigaTranscriberQtApp (окно, меню, вкладки, группы).

Оболочка — классическая (1.6): строка меню, заголовок, вкладки, нумерованные
секции. Страницы «API», «Настройки», просмотр результата и журнал с таблицей
пришли из 2.0 и живут отдельными вкладками. Редизайн 2.0 с боковой панелью и
бутафорским окном macOS снят по issue #54.
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
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import APP_TITLE, OUTPUT_FORMATS


class _CurrentPageStack(QStackedWidget):
    """Allow the current workspace to define its compact scroll-area footprint."""

    def minimumSizeHint(self):
        current = self.currentWidget()
        return current.minimumSizeHint() if current is not None else super().minimumSizeHint()

    def sizeHint(self):
        return self.minimumSizeHint()

    def hasHeightForWidth(self):
        return False

    def heightForWidth(self, _width):
        return self.minimumSizeHint().height()


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
        root_layout.addLayout(self._create_header_row())

        tabs = QTabWidget()
        tabs.tabBar().setElideMode(Qt.TextElideMode.ElideNone)
        root_layout.addWidget(tabs, 1)

        start_page = self._create_processing_start_page()
        # Result state is part of the Processing tab, populated only from real output data.
        result_page = self._create_processing_result_page()

        # Default size policy: with the 2.0 "Ignored" policy the scroll area
        # squeezed the page below its minimum and rows overlapped instead of
        # showing a scroll bar.
        self.processing_stack = _CurrentPageStack()
        self._processing_start_page = start_page
        self._processing_result_page = result_page
        self.processing_stack.addWidget(start_page)
        self.processing_stack.addWidget(result_page)
        self.processing_stack.currentChanged.connect(self.processing_stack.updateGeometry)
        proc_scroll = self._wrap_in_scroll(self.processing_stack)
        tabs.addTab(proc_scroll, "Обработка")
        live_scroll = self._wrap_in_scroll(self._create_live_tab())
        tabs.addTab(live_scroll, "Live")
        llm_scroll = self._wrap_in_scroll(self._create_llm_tab())
        tabs.addTab(llm_scroll, "LLM")
        api_tab = self._create_api_tab()
        tabs.addTab(api_tab, "API")
        log_tab = self._create_journal_tab()
        tabs.addTab(log_tab, "Журнал")
        settings_tab = self._create_settings_tab()
        tabs.addTab(settings_tab, "Настройки")
        self.tabs = tabs
        # «Настройки» — второе представление настроек вкладок «Обработка» и LLM:
        # перечитываем их при каждом открытии, а не только при старте.
        tabs.currentChanged.connect(lambda _index: self._sync_support_surface_settings())
        # Вкладки ищутся по странице, а не по номеру: номер 1 когда-то был
        # LLM, а после появления Live открывал не ту вкладку.
        self._tab_pages = {
            "processing": proc_scroll,
            "live": live_scroll,
            "llm": llm_scroll,
            "api": api_tab,
            "journal": log_tab,
            "settings": settings_tab,
        }

        # Статус-бар: краткие подсказки и состояние
        self.status_bar = self.statusBar()
        self.status_bar.showMessage(self._t("Готов к работе", "Ready to work"))

        # Диалог настроек LLM строится заранее: его поля читают настройки и
        # обработка, даже если диалог ни разу не открывали.
        self._ensure_llm_settings_dialog()
        self._apply_language()

        # Esc — отмена текущей обработки
        esc = QAction(self)
        esc.setShortcut(QKeySequence(Qt.Key.Key_Escape))
        esc.triggered.connect(self._cancel_processing)
        self.addAction(esc)

        self._apply_theme()
        self._restore_geometry()

    def _wrap_in_scroll(self, page: QWidget) -> QScrollArea:
        """Страница вкладки в прокрутке: при маленьком окне — полоса, а не наезд строк."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(page)
        return scroll

    def _create_header_row(self) -> QHBoxLayout:
        """Заголовок по центру, справа — переключатели языка и темы."""
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
        return header_row

    def _create_processing_start_page(self) -> QWidget:
        """Вкладка «Обработка»: пять нумерованных секций, запуск, прогресс, сброс."""
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
        self._bilingual(self.btn_start.setToolTip, "Начать транскрибацию выбранных файлов  (Ctrl+Enter)", "Start transcribing the selected files  (Ctrl+Enter)")
        self.btn_start.setShortcut(QKeySequence("Ctrl+Return"))
        self.btn_start.clicked.connect(self._start_processing_thread)
        main_layout.addWidget(self.btn_start)

        self._create_progress_section(main_layout)

        self.btn_clear = QPushButton("ОЧИСТИТЬ ВСЕ")
        self.btn_clear.setObjectName("clear_button")
        self.btn_clear.setFixedHeight(self._px(40))
        self._bilingual(self.btn_clear.setToolTip, "Сбросить файлы, папки, журнал и прогресс", "Reset files, folders, log and progress")
        self.btn_clear.clicked.connect(self._clear_all)
        main_layout.addWidget(self.btn_clear)

        main_layout.addStretch()
        content_widget.setObjectName("processing_page")
        return content_widget

    def _show_tab(self, name: str) -> None:
        page = getattr(self, "_tab_pages", {}).get(name)
        if page is not None:
            self.tabs.setCurrentWidget(page)

    # ──────────────────────────────────────────────────────────────
    # Секции вкладки «Обработка»: прогресс, файлы, папка, форматы
    # ──────────────────────────────────────────────────────────────

    _PROGRESS_FONT_PT = "gigaam_progress_font_pt"

    def _make_progress_bar(self, height: int, font_pt: int) -> QProgressBar:
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
        # По этому свойству _apply_theme находит шкалы и перекрашивает их тем же стилем.
        bar.setProperty(self._PROGRESS_FONT_PT, font_pt)
        self._style_progress_bar(bar)
        return bar

    def _style_progress_bar(self, bar: QProgressBar) -> None:
        """Единственный стиль шкал: «пилюля» и вертикальный градиент.

        Раньше _apply_theme перезаписывал стиль шкал своим — с радиусом _px(11)
        вместо половины высоты (прямоугольник на macOS при масштабе ≠ 1) и
        горизонтальным градиентом, который «плывёт» по мере заполнения; шкалу
        LLM он не трогал вовсе, и та оставалась в цветах прежней темы.
        """
        c = self._colors()
        radius = bar.height() // 2
        font_pt = bar.property(self._PROGRESS_FONT_PT) or 10
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

    def _create_progress_section(self, parent_layout):
        c = self._colors()
        progress_frame = QFrame()
        progress_frame.setObjectName("progress_card")
        self._progress_frame = progress_frame
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
        self._bilingual(self.btn_cancel.setToolTip, "Остановить обработку после текущего файла  (Esc)", "Stop processing after the current file  (Esc)")
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

        # Строка статуса меняется во время работы: переводит её _retranslate_shell,
        # и только пока она «пустая».
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
        self._bilingual(self.btn_open_result.setToolTip, "Открыть папку с готовыми файлами в проводнике", "Open the folder with finished files in the file manager")
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
        self._bilingual(btn_select_folder.setToolTip, "Добавить все медиафайлы из папки и подпапок", "Add all media files from the folder and subfolders")
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
        # Word wrap keeps the row's minimum width small on large system fonts;
        # otherwise the full sentence forced a horizontal scroll bar.
        self.lbl_output_folder.setWordWrap(True)
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
        self.cb_subtitle_sentence_split = QCheckBox("Разбивать по предложениям")
        self.cb_subtitle_sentence_split.setChecked(True)
        row2.addWidget(self.cb_subtitle_sentence_split)
        self.lbl_subtitle_max_lines = QLabel("Строк:")
        row2.addWidget(self.lbl_subtitle_max_lines)
        self.spin_subtitle_max_lines = QSpinBox()
        self.spin_subtitle_max_lines.setRange(1, 4)
        self.spin_subtitle_max_lines.setValue(2)
        self.spin_subtitle_max_lines.setFixedWidth(self._px(64))
        row2.addWidget(self.spin_subtitle_max_lines)
        self.lbl_subtitle_max_width = QLabel("Символов:")
        row2.addWidget(self.lbl_subtitle_max_width)
        self.spin_subtitle_max_width = QSpinBox()
        self.spin_subtitle_max_width.setRange(20, 100)
        self.spin_subtitle_max_width.setValue(64)
        self.spin_subtitle_max_width.setFixedWidth(self._px(76))
        row2.addWidget(self.spin_subtitle_max_width)
        row2.addStretch()
        layout.addLayout(row2)
        self._update_subtitle_controls_enabled()

        group.setLayout(layout)
        return group

    def _retranslate_shell(self, is_ru: bool) -> None:
        """Заголовок окна, переключатели, вкладки и строка статуса."""
        self._btn_lang.setText("EN" if is_ru else "RU")  # shows the language it switches to
        self._btn_theme.setToolTip("Переключить тему" if is_ru else "Toggle theme")
        self.setWindowTitle(APP_TITLE if is_ru else "GigaAM v3 Transcriber")
        if hasattr(self, "_title_label"):
            self._title_label.setText("GigaAMGUI v3")
        if hasattr(self, "_tab_pages"):
            for name, text in (
                ("processing", "Обработка" if is_ru else "Processing"),
                ("live", "Live"),
                ("llm", "LLM"),
                ("api", "API"),
                ("journal", "Журнал" if is_ru else "Log"),
                ("settings", "Настройки" if is_ru else "Settings"),
            ):
                self.tabs.setTabText(self.tabs.indexOf(self._tab_pages[name]), text)
        # Переводим только «пустые» состояния: статус идущей обработки или
        # текст ошибки при смене языка затирался словами «Готов к работе».
        ready = ("Готов к работе", "Ready to work")
        if hasattr(self, "status_bar") and self.status_bar.currentMessage() in ("", *ready):
            self.status_bar.showMessage(ready[0] if is_ru else ready[1])
        if hasattr(self, "lbl_status") and self.lbl_status.text() in ready:
            self.lbl_status.setText(ready[0] if is_ru else ready[1])

    def _retranslate_processing_page(self, is_ru: bool) -> None:
        """Вкладка «Обработка»: секции, кнопки, очередь, форматы."""
        if not hasattr(self, "grp_files"):
            return
        if hasattr(self, "btn_start"):
            self.btn_start.setText("ЗАПУСТИТЬ ОБРАБОТКУ" if is_ru else "START PROCESSING")
        if hasattr(self, "btn_clear"):
            self.btn_clear.setText("ОЧИСТИТЬ ВСЕ" if is_ru else "CLEAR ALL")
        self.grp_files.setTitle("1. Выбор файлов" if is_ru else "1. File selection")
        self.grp_output.setTitle("2. Папка сохранения результатов" if is_ru else "2. Output folder")
        self.grp_audio_preprocessing.setTitle("3. Подготовка аудио" if is_ru else "3. Audio preprocessing")
        self.grp_diarization.setTitle("4. Диаризация спикеров" if is_ru else "4. Speaker diarization")
        self.grp_formats.setTitle("5. Форматы вывода" if is_ru else "5. Output formats")
        self.lbl_overall.setText("Общий прогресс" if is_ru else "Overall progress")
        self.btn_select_files.setText("Выбрать файлы" if is_ru else "Choose files")
        self.btn_select_files.setToolTip("Выбрать аудио/видео файлы для обработки  (Ctrl+O)" if is_ru else "Choose audio/video files for processing  (Ctrl+O)")
        self.btn_select_folder.setText("Выбрать папку" if is_ru else "Choose folder")
        self.btn_select_folder.setToolTip("Добавить все медиафайлы из папки и подпапок" if is_ru else "Add all media files from the folder and subfolders")
        self.btn_upload.setText("Загрузить" if is_ru else "Download")
        self.btn_upload.setToolTip("Скачать медиа по ссылке и добавить в очередь" if is_ru else "Download media by URL and add it to the queue")
        self.btn_output_select.setText("Выбрать папку" if is_ru else "Choose folder")
        self.btn_open_result.setText("Открыть папку с результатами" if is_ru else "Open results folder")
        if hasattr(self, "lbl_output_folder") and (self.lbl_output_folder.text().startswith("Папка не выбрана") or self.lbl_output_folder.text().startswith("Folder not selected")):
            self.lbl_output_folder.setText("Папка не выбрана (по умолчанию - рядом с файлом)" if is_ru else "Folder not selected (default: next to the file)")
        self.btn_cancel.setText("Отменить" if is_ru else "Cancel")
        self.input_path.setPlaceholderText("Ссылка на медиа (YouTube и др.)" if is_ru else "Media URL (YouTube, etc.)")
        self.input_path.setToolTip("Вставьте ссылку и нажмите «Загрузить»" if is_ru else "Paste a link and press 'Download'")
        if not self.files_to_process:
            self.lbl_files_count.setText("Файлы не выбраны" if is_ru else "No files selected")
        self.btn_remove_file.setText("Убрать выбранное" if is_ru else "Remove selected")
        self.btn_remove_file.setToolTip("Убрать выделенные файлы из очереди  (Delete)" if is_ru else "Remove selected files from the queue  (Delete)")
        self.btn_clear_files.setText("Очистить список" if is_ru else "Clear list")
        self.btn_clear_files.setToolTip("Убрать все файлы из очереди (настройки сохранятся)" if is_ru else "Remove all files from the queue (settings will be kept)")
        self.files_list.setToolTip("Очередь файлов. Выделите и нажмите Delete, чтобы убрать." if is_ru else "File queue. Select items and press Delete to remove them.")
        if self.lbl_input_folder.text().startswith("Папка не выбрана") or self.lbl_input_folder.text().startswith("Folder not selected"):
            self.lbl_input_folder.setText("Папка не выбрана" if is_ru else "Folder not selected")
        self.drop_hint.setText("Перетащите сюда файлы или папки  ·  либо нажмите «Выбрать файлы»" if is_ru else "Drop files or folders here  ·  or click 'Choose files'")
        format_labels = {
            "txt": ("Текст", "Text"),
            "txt_timecodes": ("Таймкоды", "Timecodes"),
            "txt_diarize": ("Диар.", "Diar."),
            "txt_diarize_timecodes": ("Диар. + время", "Diar. + time"),
            "md": ("Markdown", "Markdown"),
            "srt": ("SRT", "SRT"),
            "vtt": ("VTT", "VTT"),
        }
        for fmt, cb in self.format_checkboxes.items():
            ru_label, en_label = format_labels.get(fmt, (cb.text(), cb.text()))
            cb.setText(ru_label if is_ru else en_label)
        self.cb_subtitle_sentence_split.setText(
            "Разбивать по предложениям" if is_ru else "Split by sentences"
        )
        self.lbl_subtitle_max_lines.setText(
            "Строк:" if is_ru else "Lines:"
        )
        self.lbl_subtitle_max_width.setText(
            "Символов:" if is_ru else "Characters:"
        )
