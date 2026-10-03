"""LLM-tab widget construction for the desktop application."""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..services import cli_tools


class _LlmWorkspace(QWidget):
    def hasHeightForWidth(self) -> bool:
        return False

    def heightForWidth(self, width: int) -> int:
        return self.minimumSizeHint().height()


class LlmUiMixin:
    def _create_llm_tab(self) -> QWidget:
        tab = _LlmWorkspace()
        tab.setObjectName("llm_workspace")
        tab.setMinimumWidth(0)
        tab.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(self._px(4), self._px(4), self._px(4), self._px(4))
        layout.setSpacing(self._px(2))

        heading = QHBoxLayout()
        heading.setSpacing(self._px(8))
        title = QLabel("LLM")
        title.setObjectName("llm_workspace_title")
        heading.addWidget(title)
        heading.addStretch()
        self.btn_llm_settings = QPushButton("Настройки…")
        self.btn_llm_settings.setObjectName("llm_settings_button")
        self.btn_llm_settings.setFixedHeight(self._px(22))
        self.btn_llm_settings.clicked.connect(self._open_llm_settings_dialog)
        heading.addWidget(self.btn_llm_settings)
        layout.addLayout(heading)
        layout.addWidget(self._create_llm_provider_strip())

        work_surface = QWidget()
        work_surface.setObjectName("llm_workspace_columns")
        work_surface.setMinimumWidth(0)
        work_surface.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        work_layout = QHBoxLayout(work_surface)
        work_layout.setContentsMargins(0, 0, 0, 0)
        work_layout.setSpacing(self._px(6))
        work_layout.addWidget(self._create_llm_source_group(), 4)
        work_layout.addWidget(self._create_llm_actions_group(), 3)

        result_column = QWidget()
        result_column.setObjectName("llm_result_column")
        result_column.setMinimumWidth(0)
        result_column.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        result_layout = QVBoxLayout(result_column)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(self._px(2))
        result_layout.addWidget(self._create_llm_result_group(), 1)
        result_layout.addWidget(self._create_llm_output_group())
        result_layout.addWidget(self._create_llm_save_group())
        work_layout.addWidget(result_column, 5)
        layout.addWidget(work_surface, 1)
        return tab

    # ── провайдер: общие виджеты страницы и диалога ─────────────────────

    def _ensure_llm_provider_widgets(self):
        """Комбо провайдера, поле модели и бейдж статуса живут на странице LLM;
        диалог настроек их не дублирует. Создаются один раз, кто первый спросил."""
        if getattr(self, "combo_llm_provider", None) is not None:
            return
        self.llm_provider_items = {}
        self.combo_llm_provider = QComboBox()
        self.combo_llm_provider.setObjectName("llm_provider_combo")
        for spec in cli_tools.PROVIDERS:
            self.combo_llm_provider.addItem(self._llm_provider_display_name(spec), spec.name)
        self._llm_other_index = self.combo_llm_provider.count() - 1
        self.llm_provider_items[self._llm_other_index] = self.combo_llm_provider.itemText(self._llm_other_index)
        self.combo_llm_provider.setMinimumWidth(self._px(190))
        self.combo_llm_provider.setIconSize(QSize(self._px(10), self._px(10)))

        self.entry_llm_model = QLineEdit()
        self.entry_llm_model.setObjectName("llm_model_entry")
        self.entry_llm_model.setPlaceholderText("gpt-4.1-mini / sonnet / o3 / qwen ...")

        self.lbl_llm_provider_status = QLabel("")
        self.lbl_llm_provider_status.setObjectName("llm_provider_status")
        self.lbl_llm_provider_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._llm_tool_statuses: dict[str, cli_tools.ToolStatus] = {}
        self._llm_scan_running = False

    def _llm_provider_display_name(self, spec) -> str:
        if spec.id == "other":
            return "Другое" if getattr(self, "_lang", "ru") == "ru" else "Other"
        return spec.name

    def _create_llm_provider_strip(self) -> QWidget:
        """Строка «Провайдер · статус · Модель» над рабочей областью страницы LLM."""
        self._ensure_llm_provider_widgets()
        strip = QWidget()
        strip.setObjectName("llm_provider_strip")
        row = QHBoxLayout(strip)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(self._px(6))
        self.llm_provider_labels = getattr(self, "llm_provider_labels", {})
        self.llm_provider_labels["provider"] = QLabel("Провайдер:")
        row.addWidget(self.llm_provider_labels["provider"])
        row.addWidget(self.combo_llm_provider)
        row.addWidget(self.lbl_llm_provider_status, 1)
        self.llm_provider_labels["model"] = QLabel("Модель:")
        row.addWidget(self.llm_provider_labels["model"])
        self.entry_llm_model.setMinimumWidth(self._px(140))
        row.addWidget(self.entry_llm_model, 1)
        return strip

    def _create_llm_actions_group(self) -> QGroupBox:
        group = QGroupBox("Шаблоны")
        group.setObjectName("llm_templates_panel")
        self.grp_llm_actions = group
        group.setMinimumWidth(0)
        group.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout()
        layout.setContentsMargins(self._px(4), self._px(4), self._px(4), self._px(4))
        layout.setSpacing(self._px(2))
        self.llm_action_checkboxes = {}

        for key, label, description, checked in (
            ("summary", "Выжимка", "Сжать текст до основных тезисов", True),
            ("tasks", "Задачи", "Найти поручения и action items", False),
            ("custom", "Свой промпт", "Использовать пользовательскую инструкцию", False),
        ):
            cb = QCheckBox(label)
            cb.setObjectName("llm_template_checkbox")
            cb.setChecked(checked)
            cb.setToolTip(description)
            layout.addWidget(cb)
            self.llm_action_checkboxes[key] = cb

        self.lbl_llm_actions_note = QLabel("Провайдер и свой промпт настраиваются в настройках LLM.")
        self.lbl_llm_actions_note.setObjectName("llm_templates_note")
        self.lbl_llm_actions_note.setVisible(False)
        layout.addWidget(self.lbl_llm_actions_note)

        layout.addStretch()
        self.btn_llm_process = QPushButton("Обработать")
        self.btn_llm_process.setObjectName("llm_primary_action")
        self.btn_llm_process.setFixedHeight(self._px(24))
        self.btn_llm_process.setToolTip("Запустить LLM-обработку выбранных транскриптов")
        self.btn_llm_process.clicked.connect(self._start_llm_processing)
        layout.addWidget(self.btn_llm_process)

        self.btn_llm_clear = QPushButton("Очистить")
        self.btn_llm_clear.setObjectName("llm_clear_button")
        self.btn_llm_clear.setFixedHeight(self._px(20))
        self.btn_llm_clear.setToolTip("Сбросить выбранные транскрипты, ручной текст и результат LLM")
        self.btn_llm_clear.clicked.connect(self._clear_llm_all)
        layout.addWidget(self.btn_llm_clear)
        group.setLayout(layout)
        return group

    def _create_llm_output_group(self) -> QGroupBox:
        group = QGroupBox("Сохранение")
        group.setObjectName("llm_export_destination")
        group.setTitle("")
        group.setFlat(True)
        group.setToolTip("Сохранение результата")
        self.grp_llm_output = group
        group.setMinimumWidth(0)
        group.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        layout = QHBoxLayout()
        layout.setContentsMargins(self._px(4), self._px(3), self._px(4), self._px(3))
        layout.setSpacing(self._px(6))

        self.btn_llm_output = QPushButton("Папка")
        self.btn_llm_output.setObjectName("llm_output_folder_button")
        self.btn_llm_output.setFixedHeight(self._px(20))
        self.btn_llm_output.setToolTip("Выбрать папку для результатов")
        self.btn_llm_output.clicked.connect(self._select_llm_output_folder)
        layout.addWidget(self.btn_llm_output)
        self.lbl_llm_output = QLabel("Рядом с транскриптом")
        self.lbl_llm_output.setObjectName("llm_output_path")
        self.lbl_llm_output.setMinimumWidth(0)
        layout.addWidget(self.lbl_llm_output, 1)

        self.lbl_llm_output_note = QLabel("Если папка не выбрана, результат сохраняется рядом с исходным текстом.", group)
        self.lbl_llm_output_note.setObjectName("llm_output_note")
        self.lbl_llm_output_note.setVisible(False)
        group.setLayout(layout)
        return group

    def _create_llm_save_group(self) -> QGroupBox:
        group = QGroupBox("Формат")
        group.setObjectName("llm_export_formats")
        group.setTitle("")
        group.setFlat(True)
        group.setToolTip("Форматы сохранения")
        self.grp_llm_save = group
        group.setMinimumWidth(0)
        group.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        layout = QHBoxLayout()
        layout.setContentsMargins(self._px(4), self._px(2), self._px(4), self._px(2))
        layout.setSpacing(self._px(6))
        self.llm_export_checkboxes = {}
        for key, label, checked in (("txt", "TXT", True), ("md", "MD", False), ("docx", "DOCX", False)):
            cb = QCheckBox(label)
            cb.setObjectName("llm_export_format")
            cb.setChecked(checked)
            layout.addWidget(cb)
            self.llm_export_checkboxes[key] = cb
        layout.addStretch()
        group.setLayout(layout)
        return group

    def _create_llm_source_group(self) -> QGroupBox:
        group = QGroupBox("Исходный текст")
        group.setObjectName("llm_source_panel")
        self.grp_llm_source = group
        group.setMinimumWidth(0)
        group.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout()
        layout.setContentsMargins(self._px(4), self._px(4), self._px(4), self._px(4))
        layout.setSpacing(self._px(2))

        self.btn_select_transcripts = QPushButton("Выбрать")
        self.btn_select_transcripts.setObjectName("llm_upload_button")
        self.btn_select_transcripts.setFixedHeight(self._px(22))
        self.btn_select_transcripts.setToolTip("Выбрать транскрипты")
        self.btn_select_transcripts.clicked.connect(self._select_llm_transcript_files)
        layout.addWidget(self.btn_select_transcripts)

        self.llm_drop_hint = QLabel("Перетащите или выберите")
        self.llm_drop_hint.setObjectName("llm_drop_zone")
        self.llm_drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.llm_drop_hint.setMinimumHeight(self._px(18))
        layout.addWidget(self.llm_drop_hint)

        self.llm_files_list = QListWidget()
        self.llm_files_list.setObjectName("llm_source_files")
        self.llm_files_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.llm_files_list.setMinimumHeight(self._px(24))
        self.llm_files_list.setMaximumHeight(self._px(36))
        self.llm_files_list.setToolTip("Список транскриптов. Выделите и нажмите Delete, чтобы убрать.")
        self.llm_files_list.itemSelectionChanged.connect(self._update_llm_files_controls)
        self.llm_files_list.setVisible(False)
        layout.addWidget(self.llm_files_list)
        self.llm_files_list.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)

        controls = QHBoxLayout()
        controls.setSpacing(self._px(4))
        self.lbl_llm_files_count = QLabel("Файлы не выбраны")
        self.lbl_llm_files_count.setObjectName("llm_source_summary")
        self.lbl_llm_files_count.setMinimumWidth(0)
        controls.addWidget(self.lbl_llm_files_count, 1)
        self.lbl_llm_files = self.lbl_llm_files_count
        self.btn_remove_llm_file = QPushButton("Убрать")
        self.btn_remove_llm_file.setObjectName("llm_file_control")
        self.btn_remove_llm_file.setFixedHeight(self._px(20))
        self.btn_remove_llm_file.setEnabled(False)
        self.btn_remove_llm_file.clicked.connect(self._remove_selected_llm_files)
        self.btn_remove_llm_file.setVisible(False)
        controls.addWidget(self.btn_remove_llm_file)
        self.btn_clear_llm_files = QPushButton("Очистить")
        self.btn_clear_llm_files.setObjectName("llm_file_control")
        self.btn_clear_llm_files.setFixedHeight(self._px(20))
        self.btn_clear_llm_files.setEnabled(False)
        self.btn_clear_llm_files.clicked.connect(self._clear_llm_files_list)
        self.btn_clear_llm_files.setVisible(False)
        controls.addWidget(self.btn_clear_llm_files)
        layout.addLayout(controls)

        self.lbl_llm_supported = QLabel(".txt, .md, .srt, .vtt")
        self.lbl_llm_supported.setObjectName("llm_source_hint")
        self.lbl_llm_supported.setVisible(False)
        layout.addWidget(self.lbl_llm_supported)

        self.txt_llm_transcript = QTextEdit()
        self.txt_llm_transcript.setObjectName("llm_source_editor")
        self.txt_llm_transcript.setPlaceholderText("Вставьте транскрипт")
        self.txt_llm_transcript.setMinimumHeight(self._px(40))
        layout.addWidget(self.txt_llm_transcript, 1)
        self.txt_llm_transcript.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        group.setLayout(layout)
        return group

    def _create_llm_result_group(self) -> QGroupBox:
        group = QGroupBox("Результат")
        group.setObjectName("llm_result_panel")
        self.grp_llm_result = group
        group.setMinimumWidth(0)
        group.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout()
        layout.setContentsMargins(self._px(4), self._px(4), self._px(4), self._px(4))
        layout.setSpacing(self._px(2))

        toolbar = QWidget()
        toolbar.setObjectName("llm_result_toolbar")
        result_toolbar = QHBoxLayout(toolbar)
        result_toolbar.setContentsMargins(0, 0, 0, 0)
        result_toolbar.setSpacing(self._px(4))
        self.btn_llm_copy = QPushButton("Копия")
        self.btn_llm_copy.setObjectName("llm_result_action")
        self.btn_llm_copy.setFixedHeight(self._px(20))
        self.btn_llm_copy.clicked.connect(self._copy_llm_result)
        result_toolbar.addWidget(self.btn_llm_copy)
        self.btn_llm_export_txt = QPushButton("TXT")
        self.btn_llm_export_txt.setObjectName("llm_result_action")
        self.btn_llm_export_txt.setFixedHeight(self._px(20))
        self.btn_llm_export_txt.clicked.connect(lambda: self._export_llm_result("txt"))
        result_toolbar.addWidget(self.btn_llm_export_txt)
        self.btn_llm_export_md = QPushButton("MD")
        self.btn_llm_export_md.setObjectName("llm_result_action")
        self.btn_llm_export_md.setFixedHeight(self._px(20))
        self.btn_llm_export_md.clicked.connect(lambda: self._export_llm_result("md"))
        result_toolbar.addWidget(self.btn_llm_export_md)
        layout.addWidget(toolbar)

        self.lbl_llm_status = QLabel("Готово к LLM-обработке")
        self.lbl_llm_status.setWordWrap(True)
        self.lbl_llm_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_llm_status.setObjectName("llm_result_status")
        layout.addWidget(self.lbl_llm_status)

        self.progress_bar_llm = self._make_progress_bar(height=10, font_pt=8)
        self.progress_bar_llm.setObjectName("llm_result_progress")
        layout.addWidget(self.progress_bar_llm)

        self.txt_llm_result = QTextEdit()
        self.txt_llm_result.setObjectName("llm_result_editor")
        self.txt_llm_result.setReadOnly(True)
        self.txt_llm_result.setFont(self._font(9, fixed=True))
        self.txt_llm_result.setMinimumHeight(self._px(40))
        layout.addWidget(self.txt_llm_result, 1)
        self.txt_llm_result.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        group.setLayout(layout)
        return group

    def _copy_llm_result(self) -> None:
        result_text = self.txt_llm_result.toPlainText().strip()
        if not result_text:
            QMessageBox.information(self, self._t("Внимание", "Attention"), self._t("Нет результата для копирования", "There is no result to copy."))
            return
        QApplication.clipboard().setText(result_text)
        self.lbl_llm_status.setText(self._t("Результат скопирован", "Result copied"))

    def _retranslate_llm_page(self, is_ru: bool) -> None:
        """Страница LLM: источник, шаблоны, результат, сохранение."""
        if not hasattr(self, "grp_llm_source"):
            return
        if hasattr(self, "btn_llm_process"):
            self.btn_llm_process.setText("ОБРАБОТАТЬ" if is_ru else "PROCESS")
        if hasattr(self, "btn_llm_clear"):
            self.btn_llm_clear.setText("ОЧИСТИТЬ ВСЕ" if is_ru else "CLEAR ALL")
        self.grp_llm_source.setTitle("1. Источник транскрипта" if is_ru else "1. Transcript source")
        self.grp_llm_output.setTitle("2. Куда сохранить" if is_ru else "2. Save location")
        self.grp_llm_actions.setTitle("3. Что сделать" if is_ru else "3. What to do")
        self.grp_llm_save.setTitle("4. Форматы вывода" if is_ru else "4. Output formats")
        self.grp_llm_result.setTitle("5. Результат LLM" if is_ru else "5. LLM result")
        self.btn_select_transcripts.setText("Выбрать транскрипты" if is_ru else "Choose transcripts")
        self.btn_llm_output.setText("Выбрать папку" if is_ru else "Choose folder")
        self.btn_llm_process.setToolTip("Запустить LLM-обработку выбранных транскриптов" if is_ru else "Run LLM processing for selected transcripts")
        self.btn_llm_clear.setToolTip("Сбросить выбранные транскрипты, ручной текст и результат LLM" if is_ru else "Reset selected transcripts, manual text and LLM result")
        if hasattr(self, "lbl_llm_summary_prompt"):
            self.lbl_llm_summary_prompt.setText("Промпт для выжимки:" if is_ru else "Prompt for summary:")
        if hasattr(self, "lbl_llm_tasks_prompt"):
            self.lbl_llm_tasks_prompt.setText("Промпт для задач:" if is_ru else "Prompt for tasks:")
        if hasattr(self, "lbl_llm_custom_prompt"):
            self.lbl_llm_custom_prompt.setText("Свой промпт:" if is_ru else "Custom prompt:")
        self.lbl_llm_supported.setText("Поддерживаемые файлы: .txt, .md, .srt, .vtt — либо вставьте транскрипт вручную ниже" if is_ru else "Supported files: .txt, .md, .srt, .vtt — or paste the transcript manually below")
        llm_ready = ("Готово к LLM-обработке", "Ready for LLM processing")
        if self.lbl_llm_status.text() in llm_ready:
            self.lbl_llm_status.setText(llm_ready[0] if is_ru else llm_ready[1])
        if hasattr(self, "llm_drop_hint"):
            self.llm_drop_hint.setText("Перетащите или выберите" if is_ru else "Drop or choose")
        if hasattr(self, "btn_remove_llm_file"):
            self.btn_remove_llm_file.setText("Убрать" if is_ru else "Remove")
        if hasattr(self, "btn_clear_llm_files"):
            self.btn_clear_llm_files.setText("Очистить" if is_ru else "Clear")
        if hasattr(self, "llm_files_list"):
            self.llm_files_list.setToolTip("Список транскриптов. Выделите и нажмите Delete, чтобы убрать." if is_ru else "Transcript list. Select items and press Delete to remove them.")
        self.lbl_llm_files.setText("Файлы не выбраны" if is_ru and not self.transcript_files_for_llm else ("No files selected" if not is_ru and not self.transcript_files_for_llm else self.lbl_llm_files.text()))
        if hasattr(self, "lbl_llm_files_count") and not self.transcript_files_for_llm:
            self.lbl_llm_files_count.setText("Файлы не выбраны" if is_ru else "No files selected")
        self.txt_llm_transcript.setPlaceholderText(
            "Вставьте транскрипт" if is_ru else "Paste transcript"
        )
        if hasattr(self, "llm_action_checkboxes"):
            self.llm_action_checkboxes["summary"].setText("Выжимка" if is_ru else "Summary")
            self.llm_action_checkboxes["tasks"].setText("Задачи" if is_ru else "Tasks")
            self.llm_action_checkboxes["custom"].setText("Свой промпт" if is_ru else "Custom prompt")
        if hasattr(self, "lbl_llm_actions_note"):
            self.lbl_llm_actions_note.setText("Отметьте один или несколько режимов обработки. Для «Свой промпт» текст задается в меню «Настройки → LLM API…»." if is_ru else "Select one or more processing modes. For 'Custom prompt', set the text in Settings → LLM API…")
        if hasattr(self, "lbl_llm_output") and (self.lbl_llm_output.text().startswith("Папка не выбрана") or self.lbl_llm_output.text().startswith("Folder not selected")):
            self.lbl_llm_output.setText("Папка не выбрана (по умолчанию - рядом с транскриптом)" if is_ru else "Folder not selected (default: next to the transcript)")
        if hasattr(self, "lbl_llm_output_note"):
            self.lbl_llm_output_note.setText("Если папка не выбрана, результат будет сохранен рядом с исходным транскриптом." if is_ru else "If no folder is selected, the result will be saved next to the source transcript.")
        if hasattr(self, "llm_export_checkboxes"):
            self.llm_export_checkboxes["txt"].setText("TXT (.txt)")
            self.llm_export_checkboxes["md"].setText("Markdown (.md)")
            self.llm_export_checkboxes["docx"].setText("DOCX (.docx)")
