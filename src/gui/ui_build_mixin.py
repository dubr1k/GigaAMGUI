"""Light Liquid Glass desktop shell for GigaAM v3."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import __version__ as APP_VERSION
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
        compact_width = max(760, self._px(760))
        compact_height = max(440, self._px(440))
        self.setMinimumSize(compact_width, compact_height)
        self.resize(compact_width, compact_height)
        self._build_menu_bar()
        self.menuBar().setVisible(False)

        root = QWidget()
        root.setObjectName("app_background")
        root_layout = QVBoxLayout(root)
        self._root_layout = root_layout
        root_layout.setContentsMargins(self._px(18), self._px(18), self._px(18), self._px(18))
        root_layout.setSpacing(0)
        self.setCentralWidget(root)

        app_window = QFrame()
        app_window.setObjectName("app_window")
        app_window_layout = QVBoxLayout(app_window)
        app_window_layout.setContentsMargins(0, 0, 0, 0)
        app_window_layout.setSpacing(0)
        self._app_window = app_window
        root_layout.addWidget(app_window)

        chrome = QFrame()
        chrome.setObjectName("window_chrome")
        chrome.setFixedHeight(self._px(34))
        chrome_layout = QHBoxLayout(chrome)
        chrome_layout.setContentsMargins(self._px(14), 0, self._px(14), 0)
        chrome_layout.setSpacing(self._px(7))
        for color in ("#FF5F57", "#FEBC2E", "#28C840"):
            dot = QLabel("●")
            dot.setObjectName("window_dot")
            dot.setStyleSheet(f"color: {color};")
            chrome_layout.addWidget(dot)
        chrome_title = QLabel("GigaAMGUI v3")
        chrome_title.setObjectName("window_title")
        chrome_layout.addWidget(chrome_title)
        chrome_layout.addStretch()
        self._chrome_layout = chrome_layout
        app_window_layout.addWidget(chrome)

        workspace = QWidget()
        workspace.setObjectName("app_workspace")
        workspace_layout = QHBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        self._workspace_layout = workspace_layout
        app_window_layout.addWidget(workspace, 1)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(self._px(154))
        self._sidebar = sidebar
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(self._px(13), self._px(14), self._px(13), self._px(12))
        sidebar_layout.setSpacing(self._px(4))
        brand_row = QHBoxLayout()
        brand_row.setSpacing(self._px(7))
        brand_mark = QLabel("▮▯▮")
        brand_mark.setObjectName("brand_mark")
        brand_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_mark.setFixedSize(self._px(30), self._px(30))
        brand_row.addWidget(brand_mark)
        brand_column = QVBoxLayout()
        brand_column.setContentsMargins(0, 0, 0, 0)
        brand_column.setSpacing(0)
        self._title_label = QLabel("GigaAMGUI v3")
        self._title_label.setObjectName("brand_title")
        brand_column.addWidget(self._title_label)
        self._sidebar_subtitle = QLabel("Транскрибация аудио и видео.\nБыстро. Точно. Удобно.")
        self._sidebar_subtitle.setObjectName("brand_subtitle")
        brand_column.addWidget(self._sidebar_subtitle)
        brand_row.addLayout(brand_column, 1)
        sidebar_layout.addLayout(brand_row)
        sidebar_layout.addSpacing(self._px(18))

        self._tab_labels = (
            ("Обработка", "Process"), ("Live", "Live"), ("LLM", "LLM"),
            ("API", "API"), ("Журнал", "Log"), ("Настройки", "Settings"),
        )
        self._nav_icons = ("▧", "♩", "✦", "⌘", "◷", "⚙")
        self._nav_buttons = []
        for tab_index, labels in enumerate(self._tab_labels):
            nav_button = QPushButton(f"{self._nav_icons[tab_index]}   {labels[0]}")
            nav_button.setObjectName("nav_button")
            nav_button.setCheckable(True)
            nav_button.setAutoExclusive(True)
            nav_button.setFixedHeight(self._px(32))
            nav_button.setCursor(Qt.CursorShape.PointingHandCursor)
            nav_button.clicked.connect(
                lambda _checked=False, index=tab_index: self.tabs.setCurrentIndex(index)
            )
            self._nav_buttons.append(nav_button)
            sidebar_layout.addWidget(nav_button)
        sidebar_layout.addStretch()
        self._sidebar_footer = QLabel("GigaAMGUI v3\nЛокально. Быстро. Точно.")
        self._sidebar_footer.setObjectName("sidebar_footer")
        sidebar_layout.addWidget(self._sidebar_footer)
        workspace_layout.addWidget(sidebar)

        content_shell = QWidget()
        content_shell.setObjectName("content_shell")
        content_layout = QVBoxLayout(content_shell)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(self._px(10))
        toolbar = QFrame()
        toolbar.setObjectName("toolbar")
        toolbar.setFixedHeight(self._px(1))
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        self._toolbar_context = QLabel("Рабочая область")
        self._toolbar_context.setObjectName("toolbar_context")
        toolbar_layout.addWidget(self._toolbar_context)
        self._global_search = QLineEdit()
        self._global_search.setObjectName("toolbar_search")
        self._global_search.setPlaceholderText("Поиск файлов, транскрипций, заметок…")
        self._global_search.setClearButtonEnabled(True)
        self._global_search.setToolTip("Поиск по текущему рабочему контексту")
        toolbar_layout.addWidget(self._global_search)
        self._btn_lang = QPushButton("МК")
        self._btn_lang.setObjectName("profile_button")
        self._btn_lang.setFixedSize(self._px(27), self._px(27))
        self._btn_lang.setToolTip("Switch language")
        self._btn_lang.clicked.connect(self._toggle_language)
        self._btn_theme = QPushButton("⌕")
        self._btn_theme.setObjectName("chrome_button")
        self._btn_theme.setFixedSize(self._px(27), self._px(27))
        self._btn_theme.setToolTip("Поиск")
        self._btn_theme.setEnabled(False)
        toolbar_layout.addWidget(self._btn_lang)
        toolbar_layout.addWidget(self._btn_theme)
        toolbar.setVisible(False)
        chrome_layout.addWidget(QLabel("⌕"))
        chrome_layout.addWidget(QLabel("♧"))
        chrome_layout.addWidget(self._btn_lang)
        chrome_layout.addWidget(self._btn_theme)
        content_layout.addWidget(toolbar)

        tabs = QTabWidget()
        tabs.setObjectName("main_tabs")
        tabs.tabBar().hide()
        tabs.tabBar().setElideMode(Qt.TextElideMode.ElideNone)
        self.tabs = tabs
        content_layout.addWidget(tabs, 1)
        workspace_layout.addWidget(content_shell, 1)

        # Processing start state.
        start_page = QWidget()
        start_page.setObjectName("processing_page")
        start_page.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        start_layout = QVBoxLayout(start_page)
        self._processing_start_layout = start_layout
        start_layout.setContentsMargins(self._px(16), self._px(14), self._px(16), self._px(16))
        start_layout.setSpacing(self._px(10))
        self._hero_title = QLabel("Обработка")
        self._hero_title.setObjectName("page_title")
        self._hero_title.setVisible(False)
        self._hero_subtitle = QLabel("Загрузите аудио или видео и настройте параметры.")
        self._hero_subtitle.setObjectName("page_subtitle")
        self._hero_subtitle.setVisible(False)

        workspace_row = QHBoxLayout()
        self._workspace_row = workspace_row
        workspace_row.setSpacing(self._px(10))
        files_column = QWidget()
        files_column.setObjectName("processing_files_column")
        files_column_layout = QVBoxLayout(files_column)
        files_column_layout.setContentsMargins(0, 0, 0, 0)
        files_column_layout.setSpacing(self._px(10))
        self._files_column_layout = files_column_layout
        files_column_layout.addWidget(self._create_files_group())
        workspace_row.addWidget(files_column, 3)
        settings_panel = QFrame()
        settings_panel.setObjectName("processing_settings_panel")
        settings_layout = QVBoxLayout(settings_panel)
        settings_layout.setContentsMargins(self._px(14), self._px(13), self._px(14), self._px(13))
        settings_layout.setSpacing(self._px(4))
        settings_title = QLabel("Настройки обработки")
        settings_title.setObjectName("section_title")
        settings_layout.addWidget(settings_title)
        settings_layout.addWidget(self._create_audio_preprocessing_group())
        settings_layout.addWidget(self._create_diarization_group())
        settings_layout.addWidget(self._create_formats_group())
        # Without a stretch the spare height is split between the cards, which
        # leaves each group floating in empty space on small fonts (#54).
        settings_layout.addStretch(1)
        workspace_row.addWidget(settings_panel, 2)
        start_layout.addLayout(workspace_row, 1)

        queue_panel = QFrame()
        queue_panel.setObjectName("dense_panel")
        queue_layout = QVBoxLayout(queue_panel)
        queue_layout.setContentsMargins(self._px(20), self._px(16), self._px(20), self._px(12))
        queue_layout.setSpacing(self._px(8))
        queue_head = QHBoxLayout()
        queue_title = QLabel("Выбранные файлы")
        queue_title.setObjectName("section_title")
        queue_head.addWidget(queue_title)
        queue_head.addStretch()
        self.lbl_files_count = QLabel("Файлы не выбраны")
        self.lbl_files_count.setObjectName("muted_label")
        queue_head.addWidget(self.lbl_files_count)
        self.btn_remove_file = QPushButton("Убрать выбранное")
        self.btn_remove_file.setObjectName("text_button")
        self.btn_remove_file.setEnabled(False)
        self.btn_remove_file.clicked.connect(self._remove_selected_files)
        queue_head.addWidget(self.btn_remove_file)
        self.btn_clear_files = QPushButton("Очистить")
        self.btn_clear_files.setObjectName("text_button")
        self.btn_clear_files.setEnabled(False)
        self.btn_clear_files.clicked.connect(self._clear_files_list)
        queue_head.addWidget(self.btn_clear_files)
        queue_layout.addLayout(queue_head)
        self.files_list = QListWidget()
        self.files_list.setObjectName("files_list")
        self.files_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.files_list.setMinimumHeight(self._px(58))
        self.files_list.setMaximumHeight(self._px(174))
        self.files_list.setToolTip("Очередь файлов. Выделите и нажмите Delete, чтобы убрать.")
        self.files_list.itemSelectionChanged.connect(self._update_files_controls)
        self.files_list.setVisible(False)
        queue_layout.addWidget(self.files_list)
        self._files_column_layout.addWidget(queue_panel)
        self._files_column_layout.addWidget(self._create_output_group())
        action_row = QHBoxLayout()
        action_row.setSpacing(self._px(6))
        self.btn_start = QPushButton("Запустить обработку")
        self.btn_start.setObjectName("start_button")
        self.btn_start.setFixedHeight(self._px(38))
        self.btn_start.setToolTip("Начать транскрибацию выбранных файлов  (Ctrl+Enter)")
        self.btn_start.setShortcut(QKeySequence("Ctrl+Return"))
        self.btn_start.clicked.connect(self._start_processing_thread)
        action_row.addWidget(self.btn_start, 1)
        self.btn_clear = QPushButton("Сброс")
        self.btn_clear.setObjectName("text_button")
        self.btn_clear.setFixedHeight(self._px(28))
        self.btn_clear.setToolTip("Сбросить очередь и параметры обработки")
        self.btn_clear.clicked.connect(self._clear_all)
        action_row.addWidget(self.btn_clear)
        settings_layout.addLayout(action_row)
        self._create_progress_section(start_layout)
        self._progress_frame.setVisible(False)

        # Result state is part of the Processing tab, populated only from real output data.
        result_page = QWidget()
        result_page.setObjectName("processing_result_page")
        result_layout = QVBoxLayout(result_page)
        result_layout.setContentsMargins(self._px(16), self._px(14), self._px(16), self._px(16))
        result_layout.setSpacing(self._px(10))
        result_head = QHBoxLayout()
        result_head.setSpacing(self._px(8))
        result_title_col = QVBoxLayout()
        result_title_col.setSpacing(0)
        self.result_title = QLabel("Результат обработки")
        self.result_title.setObjectName("section_title")
        result_title_col.addWidget(self.result_title)
        self.result_meta = QLabel("")
        self.result_meta.setObjectName("muted_label")
        result_title_col.addWidget(self.result_meta)
        result_head.addLayout(result_title_col, 1)
        self.result_file_picker = QComboBox()
        self.result_file_picker.setObjectName("result_file_picker")
        self.result_file_picker.setMinimumWidth(self._px(170))
        self.result_file_picker.currentIndexChanged.connect(self._select_processing_result)
        result_head.addWidget(self.result_file_picker)
        self.btn_back_to_processing = QPushButton("К обработке")
        self.btn_back_to_processing.setObjectName("secondary_button")
        self.btn_back_to_processing.clicked.connect(lambda: self.processing_stack.setCurrentWidget(self._processing_start_page))
        result_head.addWidget(self.btn_back_to_processing)
        result_layout.addLayout(result_head)
        result_body = QHBoxLayout()
        result_body.setSpacing(self._px(10))
        result_left = QVBoxLayout()
        result_left.setSpacing(self._px(8))
        player_panel = QFrame()
        player_panel.setObjectName("glass_panel")
        player_layout = QVBoxLayout(player_panel)
        player_layout.setContentsMargins(self._px(14), self._px(12), self._px(14), self._px(12))
        player_layout.setSpacing(self._px(7))
        self.result_media_name = QLabel("")
        self.result_media_name.setObjectName("section_title")
        player_layout.addWidget(self.result_media_name)
        self.result_timeline = QSlider(Qt.Orientation.Horizontal)
        self.result_timeline.setObjectName("waveform_timeline")
        self.result_timeline.setRange(0, 0)
        self.result_timeline.sliderReleased.connect(self._seek_result_playback)
        player_layout.addWidget(self.result_timeline)
        time_row = QHBoxLayout()
        self.result_position_label = QLabel("00:00 / 00:00")
        self.result_position_label.setObjectName("muted_label")
        time_row.addWidget(self.result_position_label)
        time_row.addStretch()
        self.result_player_status = QLabel("")
        self.result_player_status.setObjectName("muted_label")
        time_row.addWidget(self.result_player_status)
        player_layout.addLayout(time_row)
        player_controls = QHBoxLayout()
        player_controls.setSpacing(self._px(6))
        self.btn_result_back = QPushButton("−10")
        self.btn_result_back.setObjectName("secondary_button")
        self.btn_result_back.clicked.connect(lambda: self._seek_result_by(-10_000))
        player_controls.addWidget(self.btn_result_back)
        self.btn_result_play = QPushButton("▶")
        self.btn_result_play.setObjectName("primary_button")
        self.btn_result_play.clicked.connect(self._toggle_result_playback)
        player_controls.addWidget(self.btn_result_play)
        self.btn_result_forward = QPushButton("+10")
        self.btn_result_forward.setObjectName("secondary_button")
        self.btn_result_forward.clicked.connect(lambda: self._seek_result_by(10_000))
        player_controls.addWidget(self.btn_result_forward)
        self.result_speed = QComboBox()
        self.result_speed.setObjectName("compact_select")
        for speed in (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0):
            self.result_speed.addItem(f"{speed:g}x", speed)
        self.result_speed.currentIndexChanged.connect(self._set_result_playback_rate)
        player_controls.addWidget(self.result_speed)
        player_controls.addStretch()
        self.result_volume = QSlider(Qt.Orientation.Horizontal)
        self.result_volume.setObjectName("volume_slider")
        self.result_volume.setRange(0, 100)
        self.result_volume.setValue(100)
        self.result_volume.setFixedWidth(self._px(90))
        self.result_volume.valueChanged.connect(self._set_result_volume)
        player_controls.addWidget(self.result_volume)
        player_layout.addLayout(player_controls)
        result_left.addWidget(player_panel)
        self.result_tabs = QTabWidget()
        self.result_tabs.setObjectName("result_tabs")
        self.result_tabs.setUsesScrollButtons(False)
        self.result_transcript = QWidget()
        self.result_transcript_layout = QVBoxLayout(self.result_transcript)
        self.result_transcript_layout.setContentsMargins(self._px(8), self._px(8), self._px(8), self._px(6))
        self.result_transcript_layout.setSpacing(self._px(8))
        self.result_transcript_layout.addStretch()
        self.result_tabs.addTab(self.result_transcript, "Текст")
        self.result_srt = QTextEdit()
        self.result_srt.setObjectName("result_document")
        self.result_srt.setReadOnly(True)
        self.result_tabs.addTab(self.result_srt, "SRT")
        self.result_diarization = QTextEdit()
        self.result_diarization.setObjectName("result_document")
        self.result_diarization.setReadOnly(True)
        self.result_tabs.addTab(self.result_diarization, "Диар.")
        self.result_summary = QTextEdit()
        self.result_summary.setObjectName("result_document")
        self.result_summary.setReadOnly(True)
        self.result_tabs.addTab(self.result_summary, "Итог")
        self.result_json = QTextEdit()
        self.result_json.setObjectName("result_document")
        self.result_json.setReadOnly(True)
        self.result_json.setFont(self._font(9, fixed=True))
        self.result_tabs.addTab(self.result_json, "JSON")
        result_left.addWidget(self.result_tabs, 1)
        result_body.addLayout(result_left, 5)
        result_rail = QFrame()
        result_rail.setObjectName("dense_panel")
        result_rail.setMinimumWidth(self._px(185))
        result_rail.setMaximumWidth(self._px(220))
        rail_layout = QVBoxLayout(result_rail)
        rail_layout.setContentsMargins(self._px(12), self._px(12), self._px(12), self._px(12))
        rail_layout.setSpacing(self._px(7))
        rail_actions_title = QLabel("Действия")
        rail_actions_title.setObjectName("section_title")
        rail_layout.addWidget(rail_actions_title)
        self.result_actions_layout = QVBoxLayout()
        self.result_actions_layout.setSpacing(self._px(2))
        rail_layout.addLayout(self.result_actions_layout)
        rail_topics_title = QLabel("Ключевые темы")
        rail_topics_title.setObjectName("section_title")
        rail_layout.addWidget(rail_topics_title)
        self.result_topics = QLabel("Появятся после LLM-обработки результата.")
        self.result_topics.setObjectName("muted_label")
        self.result_topics.setWordWrap(True)
        rail_layout.addWidget(self.result_topics)
        rail_summary_title = QLabel("Краткое содержание")
        rail_summary_title.setObjectName("section_title")
        rail_layout.addWidget(rail_summary_title)
        self.result_summary_rail = QLabel("Не создавалось автоматически.")
        self.result_summary_rail.setObjectName("muted_label")
        self.result_summary_rail.setWordWrap(True)
        rail_layout.addWidget(self.result_summary_rail)
        rail_layout.addStretch()
        result_body.addWidget(result_rail)
        result_layout.addLayout(result_body, 1)

        self.processing_stack = _CurrentPageStack()
        self.processing_stack.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._processing_start_page = start_page
        self._processing_result_page = result_page
        self.processing_stack.addWidget(start_page)
        self.processing_stack.addWidget(result_page)
        self.processing_stack.currentChanged.connect(self.processing_stack.updateGeometry)
        proc_scroll = QScrollArea()
        proc_scroll.setWidgetResizable(True)
        proc_scroll.setFrameShape(QFrame.Shape.NoFrame)
        proc_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        proc_scroll.setWidget(self.processing_stack)
        tabs.addTab(proc_scroll, "Обработка")

        live_scroll = QScrollArea()
        live_scroll.setWidgetResizable(True)
        live_scroll.setFrameShape(QFrame.Shape.NoFrame)
        live_scroll.setWidget(self._create_live_tab())
        tabs.addTab(live_scroll, "Live")
        llm_scroll = QScrollArea()
        llm_scroll.setWidgetResizable(True)
        llm_scroll.setFrameShape(QFrame.Shape.NoFrame)
        llm_scroll.setWidget(self._create_llm_tab())
        tabs.addTab(llm_scroll, "LLM")
        tabs.addTab(self._create_api_tab(), "API")

        log_tab = QWidget()
        log_tab.setObjectName("log_page")
        log_layout = QVBoxLayout(log_tab)
        log_layout.setContentsMargins(self._px(26), self._px(20), self._px(26), self._px(24))
        log_layout.setSpacing(self._px(12))
        journal_heading = QVBoxLayout()
        journal_heading.setSpacing(self._px(2))
        self.journal_title = QLabel("Журнал")
        self.journal_title.setObjectName("page_title")
        journal_heading.addWidget(self.journal_title)
        self.journal_subtitle = QLabel("Текущие события обработки из журнала приложения.")
        self.journal_subtitle.setObjectName("page_subtitle")
        journal_heading.addWidget(self.journal_subtitle)
        log_layout.addLayout(journal_heading)

        journal_controls = QHBoxLayout()
        journal_controls.setSpacing(self._px(6))
        self._journal_filter_buttons = {}
        for key, text in (("all", "Все"), ("ready", "Готово"), ("processing", "В обработке"), ("error", "Ошибка")):
            button = QPushButton(text)
            button.setObjectName("journal_filter")
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setProperty("journal_filter", key)
            button.clicked.connect(self._filter_journal_rows)
            journal_controls.addWidget(button)
            self._journal_filter_buttons[key] = button
        self._journal_filter_buttons["all"].setChecked(True)
        journal_controls.addStretch()
        self.journal_search = QLineEdit()
        self.journal_search.setObjectName("journal_search")
        self.journal_search.setPlaceholderText("Поиск…")
        self.journal_search.setClearButtonEnabled(True)
        self.journal_search.setFixedHeight(self._px(32))
        self.journal_search.setMinimumWidth(self._px(210))
        self.journal_search.textChanged.connect(self._filter_journal_rows)
        journal_controls.addWidget(self.journal_search)
        log_layout.addLayout(journal_controls)

        self.journal_table = QTableWidget(0, 4)
        self.journal_table.setObjectName("journal_table")
        self.journal_table.setHorizontalHeaderLabels(("Файл", "Длительность", "Статус", "Дата"))
        self.journal_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.journal_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.journal_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.journal_table.setShowGrid(False)
        self.journal_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.journal_table.verticalHeader().hide()
        header = self.journal_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.journal_table.setMinimumHeight(self._px(220))
        log_layout.addWidget(self.journal_table, 1)
        self.journal_empty = QLabel("События обработки появятся здесь после запуска.")
        self.journal_empty.setObjectName("journal_empty")
        self.journal_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        log_layout.addWidget(self.journal_empty, 1)

        technical_log = QFrame()
        technical_log.setObjectName("dense_panel")
        technical_log_layout = QVBoxLayout(technical_log)
        technical_log_layout.setContentsMargins(self._px(14), self._px(10), self._px(14), self._px(10))
        technical_log_layout.setSpacing(self._px(6))
        log_toolbar = QHBoxLayout()
        self.journal_technical_title = QLabel("Технический журнал")
        self.journal_technical_title.setObjectName("section_title")
        log_toolbar.addWidget(self.journal_technical_title)
        log_toolbar.addStretch()
        self.btn_log_copy = QPushButton("Копировать")
        self.btn_log_copy.setObjectName("text_button")
        self.btn_log_copy.clicked.connect(self._copy_log)
        log_toolbar.addWidget(self.btn_log_copy)
        self.btn_log_save = QPushButton("Сохранить…")
        self.btn_log_save.setObjectName("text_button")
        self.btn_log_save.clicked.connect(self._save_log)
        log_toolbar.addWidget(self.btn_log_save)
        self.btn_log_clear = QPushButton("Очистить журнал")
        self.btn_log_clear.setObjectName("text_button")
        self.btn_log_clear.clicked.connect(self._clear_log)
        log_toolbar.addWidget(self.btn_log_clear)
        technical_log_layout.addLayout(log_toolbar)
        self.log_text = QTextEdit()
        self.log_text.setObjectName("log_document")
        self.log_text.setReadOnly(True)
        self.log_text.setFont(self._font(10, fixed=True))
        self.log_text.setMaximumHeight(self._px(150))
        technical_log_layout.addWidget(self.log_text)
        log_layout.addWidget(technical_log)
        self._journal_entries = []
        self._refresh_journal_labels()
        tabs.addTab(log_tab, "Журнал")
        tabs.addTab(self._create_settings_tab(), "Настройки")

        self.tabs.currentChanged.connect(self._sync_nav_selection)
        self.tabs.currentChanged.connect(self._update_toolbar_context)
        self.status_bar = self.statusBar()
        self.status_bar.setFixedHeight(self._px(30))
        self.status_bar.showMessage(self._t("Готов к работе", "Ready to work"))
        self._ensure_llm_settings_dialog()
        self._apply_language()
        self._apply_shell_copy()
        self._btn_lang.clicked.connect(self._apply_shell_copy)
        self._btn_lang.clicked.connect(self._refresh_journal_labels)
        self._style_drop_hint()

        esc = QAction(self)
        esc.setShortcut(QKeySequence(Qt.Key.Key_Escape))
        esc.triggered.connect(self._cancel_processing)
        self.addAction(esc)
        self._apply_theme()
        self._restore_geometry()
        self._update_responsive_layout()

    def _sync_nav_selection(self, active_index: int):
        for tab_index, nav_button in enumerate(self._nav_buttons):
            blocked = nav_button.blockSignals(True)
            nav_button.setChecked(tab_index == active_index)
            nav_button.blockSignals(blocked)

    def _update_toolbar_context(self, active_index: int):
        if hasattr(self, "_toolbar_context") and 0 <= active_index < len(self._tab_labels):
            self._toolbar_context.setText(self._tab_labels[active_index][0 if self._lang == "ru" else 1])

    def _apply_shell_copy(self):
        is_ru = self._lang == "ru"
        self._hero_title.setText("Обработка" if is_ru else "Process")
        self._hero_subtitle.setText("Загрузите аудио или видео и настройте параметры." if is_ru else "Add audio or video and choose processing options.")
        self._sidebar_subtitle.setText(
            "Транскрибация аудио и видео.\nБыстро. Точно. Удобно."
            if is_ru else
            "Audio and video transcription.\nFast. Precise. Convenient."
        )
        self._sidebar_footer.setText("GigaAMGUI v3\nЛокально. Быстро. Точно." if is_ru else "GigaAMGUI v3\nLocal. Fast. Accurate.")
        for index, (button, labels) in enumerate(zip(self._nav_buttons, self._tab_labels, strict=False)):
            button.setText(f"{self._nav_icons[index]}   {labels[0] if is_ru else labels[1]}")
        for index, labels in enumerate(self._tab_labels):
            self.tabs.setTabText(index, labels[0] if is_ru else labels[1])
        self.grp_files.setTitle("Добавление файла" if is_ru else "Add media")
        self.grp_output.setTitle("Папка сохранения" if is_ru else "Output folder")
        self.grp_audio_preprocessing.setTitle("Режим" if is_ru else "Mode")
        self.grp_diarization.setTitle("Диаризация" if is_ru else "Diarization")
        self.grp_formats.setTitle("Форматы вывода" if is_ru else "Output formats")
        self.btn_start.setText("Запустить обработку" if is_ru else "Start processing")
        self.btn_clear.setText("Сброс" if is_ru else "Reset")
        self.btn_clear.setToolTip(
            "Сбросить очередь и параметры обработки"
            if is_ru else
            "Reset the queue and processing options"
        )
        self._update_toolbar_context(self.tabs.currentIndex())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_workspace_row"):
            self._update_responsive_layout()

    def _update_responsive_layout(self):
        tight = self.width() <= self._px(1440)
        compact = self.width() <= self._px(900)
        state = (tight, compact)
        if state == getattr(self, "_responsive_layout_state", None):
            return
        self._responsive_layout_state = state
        outer_horizontal_margin = 8 if compact else (12 if tight else 18)
        outer_vertical_margin = 8 if compact else 12
        self._root_layout.setContentsMargins(
            self._px(outer_horizontal_margin),
            self._px(outer_vertical_margin),
            self._px(outer_horizontal_margin),
            self._px(outer_vertical_margin),
        )
        horizontal_margin = 4 if compact else (12 if tight else 18)
        vertical_margin = 4 if compact else (10 if tight else 14)
        self._processing_start_layout.setContentsMargins(
            self._px(horizontal_margin),
            self._px(vertical_margin),
            self._px(horizontal_margin),
            self._px(vertical_margin),
        )
        self._processing_start_layout.setSpacing(self._px(8 if tight else 10))
        self._workspace_row.setStretch(0, 3)
        self._workspace_row.setStretch(1, 2)

    def _build_menu_bar(self):
        menubar = self.menuBar()
        menubar.clear()
        self._menu_file = menubar.addMenu("Файл")
        self._act_files = QAction("Выбрать файлы…", self)
        self._act_files.setShortcut(QKeySequence.StandardKey.Open)
        self._act_files.triggered.connect(self._select_files)
        self._menu_file.addAction(self._act_files)
        self._act_folder = QAction("Выбрать папку с файлами…", self)
        self._act_folder.triggered.connect(self._select_files_folder)
        self._menu_file.addAction(self._act_folder)
        self._act_out = QAction("Папка сохранения…", self)
        self._act_out.triggered.connect(self._select_output_folder)
        self._menu_file.addAction(self._act_out)
        self._menu_file.addSeparator()
        self._act_open_res = QAction("Открыть папку с результатами", self)
        self._act_open_res.triggered.connect(self._open_results_folder)
        self._menu_file.addAction(self._act_open_res)
        self._menu_file.addSeparator()
        self._act_quit = QAction("Выход", self)
        self._act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        self._act_quit.triggered.connect(self.close)
        self._menu_file.addAction(self._act_quit)

        self._menu_view = menubar.addMenu("Вид")
        self._act_theme = QAction("Переключить тему", self)
        self._act_theme.triggered.connect(self._toggle_theme)
        self._menu_view.addAction(self._act_theme)
        self._act_accent = QAction("Акцентный цвет…", self)
        self._act_accent.triggered.connect(self._choose_accent_color)
        self._menu_view.addAction(self._act_accent)
        self._act_accent_reset = QAction("Сбросить акцентный цвет", self)
        self._act_accent_reset.triggered.connect(self._reset_accent_color)
        self._menu_view.addAction(self._act_accent_reset)

        self._menu_settings = menubar.addMenu("Настройки")
        self._act_asr_model = QAction("Модель распознавания…", self)
        self._act_asr_model.triggered.connect(self._select_asr_model)
        self._menu_settings.addAction(self._act_asr_model)
        self._act_asr_backend = QAction("Движок распознавания…", self)
        self._act_asr_backend.triggered.connect(self._select_asr_backend)
        self._menu_settings.addAction(self._act_asr_backend)
        self._act_device = QAction("Устройство (CPU / GPU)…", self)
        self._act_device.triggered.connect(self._change_device)
        self._menu_settings.addAction(self._act_device)
        self._act_data_dir = QAction("Папка данных и моделей…", self)
        self._act_data_dir.triggered.connect(self._select_data_directory)
        self._menu_settings.addAction(self._act_data_dir)
        self._act_llm = QAction("LLM API…", self)
        self._act_llm.triggered.connect(self._open_llm_settings_dialog)
        self._menu_settings.addAction(self._act_llm)

        self._menu_help = menubar.addMenu("Справка")
        self._act_about = QAction("О программе", self)
        self._act_about.triggered.connect(self._show_about)
        self._menu_help.addAction(self._act_about)

    def _show_about(self):
        QMessageBox.about(
            self, self._t("О программе", "About"),
            self._t(
                f"<b>{APP_TITLE}</b><br>Версия {APP_VERSION}<br><br>Локальная транскрибация аудио и видео на GigaAM v3.",
                f"<b>{APP_TITLE}</b><br>Version {APP_VERSION}<br><br>Local audio and video transcription powered by GigaAM v3.",
            ),
        )

    def _make_progress_bar(self, height: int, font_pt: int) -> QProgressBar:
        bar = QProgressBar()
        bar.setFixedHeight(self._px(height))
        bar.setRange(0, 100)
        return bar

    def _create_progress_section(self, parent_layout):
        progress_frame = QFrame()
        progress_frame.setObjectName("progress_card")
        self._progress_frame = progress_frame
        layout = QVBoxLayout(progress_frame)
        layout.setContentsMargins(self._px(20), self._px(14), self._px(20), self._px(14))
        layout.setSpacing(self._px(7))
        head = QHBoxLayout()
        self.lbl_overall = QLabel("Общий прогресс")
        self.lbl_overall.setObjectName("section_title")
        head.addWidget(self.lbl_overall)
        head.addStretch()
        self.lbl_file_counter = QLabel("")
        self.lbl_file_counter.setObjectName("muted_label")
        head.addWidget(self.lbl_file_counter)
        self.btn_cancel = QPushButton("Отменить")
        self.btn_cancel.setObjectName("secondary_button")
        self.btn_cancel.clicked.connect(self._cancel_processing)
        self.btn_cancel.setVisible(False)
        head.addWidget(self.btn_cancel)
        layout.addLayout(head)
        self.progress_bar_total = self._make_progress_bar(12, 9)
        layout.addWidget(self.progress_bar_total)
        self.detail_row = QWidget()
        detail = QHBoxLayout(self.detail_row)
        detail.setContentsMargins(0, 0, 0, 0)
        self.lbl_stage = QLabel("")
        self.lbl_stage.setObjectName("muted_label")
        detail.addWidget(self.lbl_stage)
        detail.addStretch()
        self.lbl_current_file = QLabel("")
        self.lbl_current_file.setObjectName("muted_label")
        detail.addWidget(self.lbl_current_file)
        layout.addWidget(self.detail_row)
        self.detail_row.setVisible(False)
        self.progress_bar_file = self._make_progress_bar(8, 8)
        layout.addWidget(self.progress_bar_file)
        self.lbl_status = QLabel(self._t("Готов к работе", "Ready to work"))
        self.lbl_status.setObjectName("muted_label")
        layout.addWidget(self.lbl_status)
        self.btn_open_result = QPushButton("Открыть папку с результатами")
        self.btn_open_result.setObjectName("text_button")
        self.btn_open_result.clicked.connect(self._open_results_folder)
        self.btn_open_result.setVisible(False)
        layout.addWidget(self.btn_open_result)
        parent_layout.addWidget(progress_frame)

    def _create_files_group(self) -> QGroupBox:
        group = QGroupBox("Добавление файла")
        group.setObjectName("glass_panel")
        group.setMinimumHeight(self._px(210))
        self.grp_files = group
        layout = QVBoxLayout(group)
        layout.setContentsMargins(self._px(20), self._px(18), self._px(20), self._px(16))
        layout.setSpacing(self._px(10))
        self.drop_hint = QLabel("⇧\n\nПеретащите сюда аудио или видео\n.wav, .mp3, .m4a, .mp4, .mov, .mkv")
        self.drop_hint.setObjectName("drop_hint")
        self.drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_hint.setWordWrap(True)
        self.drop_hint.setMinimumHeight(self._px(104))
        layout.addWidget(self.drop_hint, 1)
        actions = QHBoxLayout()
        self.btn_select_files = QPushButton("Выбрать файлы")
        self.btn_select_files.setObjectName("primary_button")
        self.btn_select_files.clicked.connect(self._select_files)
        actions.addWidget(self.btn_select_files)
        self.btn_select_folder = QPushButton("Папка")
        self.btn_select_folder.setObjectName("secondary_button")
        self.btn_select_folder.clicked.connect(self._select_files_folder)
        actions.addWidget(self.btn_select_folder)
        actions.addStretch()
        layout.addLayout(actions)
        media = QHBoxLayout()
        self.input_path = QLineEdit()
        self.input_path.setPlaceholderText("Ссылка на медиа")
        self.input_path.returnPressed.connect(self._start_download)
        media.addWidget(self.input_path, 1)
        self.btn_upload = QPushButton("Загрузить")
        self.btn_upload.setObjectName("secondary_button")
        self.btn_upload.clicked.connect(self._start_download)
        media.addWidget(self.btn_upload)
        self.progress_upload = QProgressBar()
        self.progress_upload.setFixedWidth(self._px(90))
        self.progress_upload.setVisible(False)
        media.addWidget(self.progress_upload)
        layout.addLayout(media)
        self.lbl_input_folder = QLabel("Папка не выбрана")
        self.lbl_input_folder.setObjectName("muted_label")
        layout.addWidget(self.lbl_input_folder)
        return group

    def _size_list_to_contents(self, list_widget: QListWidget, min_rows: int = 1, max_rows: int = 5):
        row_height = list_widget.sizeHintForRow(0) or self._px(32)
        count = max(min_rows, min(max_rows, list_widget.count() or min_rows))
        list_widget.setFixedHeight(row_height * count + self._px(6))

    def _style_drop_hint(self):
        c = self._colors()
        active = getattr(self, "_drop_active", False)
        if hasattr(self, "drop_hint"):
            self.drop_hint.setStyleSheet(
                f"#drop_hint {{ border: 1px dashed {c['accent'] if active else '#CCD5E1'}; border-radius: {self._px(14)}px; background: {'#EAF4FF' if active else 'rgba(255,255,255,0.38)'}; color: {c['accent'] if active else c['text_mute2']}; padding: {self._px(10)}px; }}"
            )
        if hasattr(self, "llm_drop_hint"):
            self.llm_drop_hint.setStyleSheet(
                f"#llm_drop_hint {{ border: 1px dashed {c['accent'] if active else '#CCD5E1'}; border-radius: {self._px(14)}px; background: transparent; }}"
            )

    def _create_output_group(self) -> QGroupBox:
        group = QGroupBox("Папка сохранения")
        group.setObjectName("dense_panel")
        self.grp_output = group
        layout = QHBoxLayout(group)
        layout.setContentsMargins(self._px(20), self._px(12), self._px(20), self._px(12))
        self.btn_output_select = QPushButton("Выбрать папку")
        self.btn_output_select.setObjectName("secondary_button")
        self.btn_output_select.clicked.connect(self._select_output_folder)
        layout.addWidget(self.btn_output_select)
        self.lbl_output_folder = QLabel("Папка не выбрана (по умолчанию — рядом с файлом)")
        self.lbl_output_folder.setObjectName("muted_label")
        self.lbl_output_folder.setWordWrap(True)
        layout.addWidget(self.lbl_output_folder, 1)
        return group

    def _create_formats_group(self) -> QGroupBox:
        group = QGroupBox("Форматы вывода")
        group.setObjectName("settings_group")
        self.grp_formats = group
        layout = QVBoxLayout(group)
        layout.setContentsMargins(0, self._px(2), 0, self._px(2))
        layout.setSpacing(self._px(6))
        self.format_checkboxes = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(self._px(12))
        grid.setVerticalSpacing(self._px(4))
        for index, fmt in enumerate(("txt", "txt_timecodes", "md", "srt", "vtt", "txt_diarize", "txt_diarize_timecodes")):
            checkbox = QCheckBox(OUTPUT_FORMATS[fmt])
            checkbox.setChecked(fmt in ("txt", "txt_timecodes"))
            if fmt in ("txt_diarize", "txt_diarize_timecodes"):
                checkbox.setEnabled(False)
            checkbox.stateChanged.connect(lambda _state, f=fmt: self._toggle_format(f))
            grid.addWidget(checkbox, index // 2, index % 2)
            self.format_checkboxes[fmt] = checkbox
        layout.addLayout(grid)
        self.cb_subtitle_sentence_split = QCheckBox("Разбивать по предложениям")
        self.cb_subtitle_sentence_split.setChecked(True)
        layout.addWidget(self.cb_subtitle_sentence_split)
        # Two label/spinner pairs on a grid: a single row overflowed the
        # settings column and cut off "Символов" (#54).
        options = QGridLayout()
        options.setHorizontalSpacing(self._px(6))
        options.setVerticalSpacing(self._px(2))
        self.lbl_subtitle_max_lines = QLabel("Строк:")
        self.lbl_subtitle_max_lines.setObjectName("field_label")
        options.addWidget(self.lbl_subtitle_max_lines, 0, 0)
        self.spin_subtitle_max_lines = QSpinBox()
        self.spin_subtitle_max_lines.setRange(1, 4)
        self.spin_subtitle_max_lines.setValue(2)
        options.addWidget(self.spin_subtitle_max_lines, 0, 1)
        self.lbl_subtitle_max_width = QLabel("Символов:")
        self.lbl_subtitle_max_width.setObjectName("field_label")
        options.addWidget(self.lbl_subtitle_max_width, 1, 0)
        self.spin_subtitle_max_width = QSpinBox()
        self.spin_subtitle_max_width.setRange(20, 100)
        self.spin_subtitle_max_width.setValue(64)
        options.addWidget(self.spin_subtitle_max_width, 1, 1)
        options.setColumnStretch(2, 1)
        layout.addLayout(options)
        self._update_subtitle_controls_enabled()
        return group
