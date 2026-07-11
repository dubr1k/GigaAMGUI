"""Построение вкладки LLM (виджеты) для GigaTranscriberQtApp.

Mixin: методы _create_llm_* и диалог настроек LLM. Работают со `self` главного окна.
Поведение сохранено 1:1 — методы перенесены без изменений.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class LlmUiMixin:
    def _create_llm_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(self._px(8), self._px(14), self._px(8), self._px(6))
        layout.setSpacing(self._px(4))

        layout.addWidget(self._create_llm_source_group())
        layout.addWidget(self._create_llm_output_group())
        layout.addWidget(self._create_llm_actions_group())
        layout.addWidget(self._create_llm_save_group())

        self.btn_llm_process = QPushButton("ОБРАБОТАТЬ")
        self.btn_llm_process.setObjectName("start_button")
        self.btn_llm_process.setFixedHeight(self._px(52))
        self.btn_llm_process.setToolTip("Запустить LLM-обработку выбранных транскриптов")
        self.btn_llm_process.clicked.connect(self._start_llm_processing)
        layout.addWidget(self.btn_llm_process)

        layout.addWidget(self._create_llm_result_group(), 1)

        self.btn_llm_clear = QPushButton("ОЧИСТИТЬ ВСЕ")
        self.btn_llm_clear.setObjectName("clear_button")
        self.btn_llm_clear.setFixedHeight(self._px(40))
        self.btn_llm_clear.setToolTip("Сбросить выбранные транскрипты, ручной текст и результат LLM")
        self.btn_llm_clear.clicked.connect(self._clear_llm_all)
        layout.addWidget(self.btn_llm_clear)

        return tab

    def _create_llm_api_group(self) -> QGroupBox:
        group = QGroupBox("LLM API")
        layout = QVBoxLayout()
        layout.setContentsMargins(self._px(12), self._px(8), self._px(12), self._px(10))
        layout.setSpacing(self._px(8))

        label_col = self._label_column_width(("Провайдер:", "Модель:", "API URL:", "API Key:"))
        self.llm_provider_labels = {}
        self.llm_provider_items = {5: "Другое"}

        provider_row = QHBoxLayout()
        self.llm_provider_labels["provider"] = self._form_label("Провайдер:", label_col)
        provider_row.addWidget(self.llm_provider_labels["provider"])
        self.combo_llm_provider = QComboBox()
        self.combo_llm_provider.addItems(["API", "Claude Code", "Codex", "OpenCode", "Pi", "Другое"])
        self.combo_llm_provider.setMinimumWidth(self._px(180))
        provider_row.addWidget(self.combo_llm_provider)
        provider_row.addStretch()
        layout.addLayout(provider_row)

        common_row = QHBoxLayout()
        self.llm_provider_labels["model"] = self._form_label("Модель:", label_col)
        common_row.addWidget(self.llm_provider_labels["model"])
        self.entry_llm_model = QLineEdit()
        self.entry_llm_model.setPlaceholderText("gpt-4.1-mini / sonnet / o3 / qwen ...")
        common_row.addWidget(self.entry_llm_model, 1)
        common_row.addSpacing(self._px(12))
        common_row.addWidget(QLabel("Temperature:"))
        self.entry_llm_temperature = QLineEdit()
        self.entry_llm_temperature.setMaximumWidth(self._px(110))
        common_row.addWidget(self.entry_llm_temperature)
        layout.addLayout(common_row)

        self.llm_api_settings_widget = QWidget()
        self.llm_api_settings_widget.setStyleSheet("background: transparent;")
        api_layout = QVBoxLayout(self.llm_api_settings_widget)
        api_layout.setContentsMargins(0, 0, 0, 0)
        api_layout.setSpacing(self._px(8))
        row1 = QHBoxLayout()
        row1.addWidget(self._form_label("API URL:", label_col))
        self.entry_llm_api_url = QLineEdit()
        self.entry_llm_api_url.setPlaceholderText("https://api.openai.com/v1")
        row1.addWidget(self.entry_llm_api_url, 1)
        api_layout.addLayout(row1)
        row2 = QHBoxLayout()
        row2.addWidget(self._form_label("API Key:", label_col))
        self.entry_llm_api_key = QLineEdit()
        self.entry_llm_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.entry_llm_api_key.setPlaceholderText("Bearer token / API key")
        row2.addWidget(self.entry_llm_api_key, 1)
        api_layout.addLayout(row2)
        layout.addWidget(self.llm_api_settings_widget)

        self.llm_claude_settings_widget = QWidget()
        self.llm_claude_settings_widget.setStyleSheet("background: transparent;")
        claude_layout = QVBoxLayout(self.llm_claude_settings_widget)
        claude_layout.setContentsMargins(0, 0, 0, 0)
        claude_layout.setSpacing(self._px(8))
        claude_col = self._label_column_width(("Claude Code путь:", "Claude доп. аргументы:"))
        row4 = QHBoxLayout()
        self.llm_provider_labels["claude_path"] = self._form_label("Claude Code путь:", claude_col)
        row4.addWidget(self.llm_provider_labels["claude_path"])
        self.entry_llm_claude_path = QLineEdit()
        self.entry_llm_claude_path.setPlaceholderText("claude")
        row4.addWidget(self.entry_llm_claude_path, 1)
        claude_layout.addLayout(row4)
        row5 = QHBoxLayout()
        self.llm_provider_labels["claude_args"] = self._form_label("Claude доп. аргументы:", claude_col)
        row5.addWidget(self.llm_provider_labels["claude_args"])
        self.entry_llm_claude_args = QLineEdit()
        self.entry_llm_claude_args.setPlaceholderText("например: --permission-mode bypassPermissions")
        row5.addWidget(self.entry_llm_claude_args, 1)
        claude_layout.addLayout(row5)
        layout.addWidget(self.llm_claude_settings_widget)

        self.llm_codex_settings_widget = QWidget()
        self.llm_codex_settings_widget.setStyleSheet("background: transparent;")
        codex_layout = QVBoxLayout(self.llm_codex_settings_widget)
        codex_layout.setContentsMargins(0, 0, 0, 0)
        codex_layout.setSpacing(self._px(8))
        codex_col = self._label_column_width(("Codex путь:", "Codex доп. аргументы:"))
        row6 = QHBoxLayout()
        self.llm_provider_labels["codex_path"] = self._form_label("Codex путь:", codex_col)
        row6.addWidget(self.llm_provider_labels["codex_path"])
        self.entry_llm_codex_path = QLineEdit()
        self.entry_llm_codex_path.setPlaceholderText("codex")
        row6.addWidget(self.entry_llm_codex_path, 1)
        codex_layout.addLayout(row6)
        row7 = QHBoxLayout()
        self.llm_provider_labels["codex_args"] = self._form_label("Codex доп. аргументы:", codex_col)
        row7.addWidget(self.llm_provider_labels["codex_args"])
        self.entry_llm_codex_args = QLineEdit()
        self.entry_llm_codex_args.setPlaceholderText("например: --dangerously-bypass-approvals-and-sandbox")
        row7.addWidget(self.entry_llm_codex_args, 1)
        codex_layout.addLayout(row7)
        layout.addWidget(self.llm_codex_settings_widget)

        self.llm_opencode_settings_widget = QWidget()
        self.llm_opencode_settings_widget.setStyleSheet("background: transparent;")
        opencode_layout = QVBoxLayout(self.llm_opencode_settings_widget)
        opencode_layout.setContentsMargins(0, 0, 0, 0)
        opencode_layout.setSpacing(self._px(8))
        opencode_col = self._label_column_width(("OpenCode путь:", "OpenCode доп. аргументы:"))
        row8 = QHBoxLayout()
        self.llm_provider_labels["opencode_path"] = self._form_label("OpenCode путь:", opencode_col)
        row8.addWidget(self.llm_provider_labels["opencode_path"])
        self.entry_llm_opencode_path = QLineEdit()
        self.entry_llm_opencode_path.setPlaceholderText("opencode")
        row8.addWidget(self.entry_llm_opencode_path, 1)
        opencode_layout.addLayout(row8)
        row9 = QHBoxLayout()
        self.llm_provider_labels["opencode_args"] = self._form_label("OpenCode доп. аргументы:", opencode_col)
        row9.addWidget(self.llm_provider_labels["opencode_args"])
        self.entry_llm_opencode_args = QLineEdit()
        self.entry_llm_opencode_args.setPlaceholderText("например: --print")
        row9.addWidget(self.entry_llm_opencode_args, 1)
        opencode_layout.addLayout(row9)
        layout.addWidget(self.llm_opencode_settings_widget)

        self.llm_pi_settings_widget = QWidget()
        self.llm_pi_settings_widget.setStyleSheet("background: transparent;")
        pi_layout = QVBoxLayout(self.llm_pi_settings_widget)
        pi_layout.setContentsMargins(0, 0, 0, 0)
        pi_layout.setSpacing(self._px(8))
        pi_col = self._label_column_width(("Pi путь:", "Pi provider:", "Pi доп. аргументы:"))
        row10 = QHBoxLayout()
        self.llm_provider_labels["pi_path"] = self._form_label("Pi путь:", pi_col)
        row10.addWidget(self.llm_provider_labels["pi_path"])
        self.entry_llm_pi_path = QLineEdit()
        self.entry_llm_pi_path.setPlaceholderText("pi")
        row10.addWidget(self.entry_llm_pi_path, 1)
        pi_layout.addLayout(row10)
        row11 = QHBoxLayout()
        self.llm_provider_labels["pi_provider"] = self._form_label("Pi provider:", pi_col)
        row11.addWidget(self.llm_provider_labels["pi_provider"])
        self.entry_llm_pi_provider = QLineEdit()
        self.entry_llm_pi_provider.setPlaceholderText("openai / anthropic / google ...")
        row11.addWidget(self.entry_llm_pi_provider, 1)
        pi_layout.addLayout(row11)
        row12 = QHBoxLayout()
        self.llm_provider_labels["pi_args"] = self._form_label("Pi доп. аргументы:", pi_col)
        row12.addWidget(self.llm_provider_labels["pi_args"])
        self.entry_llm_pi_args = QLineEdit()
        self.entry_llm_pi_args.setPlaceholderText("например: --no-tools --thinking low")
        row12.addWidget(self.entry_llm_pi_args, 1)
        pi_layout.addLayout(row12)
        layout.addWidget(self.llm_pi_settings_widget)

        self.llm_other_settings_widget = QWidget()
        self.llm_other_settings_widget.setStyleSheet("background: transparent;")
        other_layout = QVBoxLayout(self.llm_other_settings_widget)
        other_layout.setContentsMargins(0, 0, 0, 0)
        other_layout.setSpacing(self._px(8))
        other_col = self._label_column_width(("Команда:", "Аргументы:"))
        row13 = QHBoxLayout()
        self.llm_provider_labels["other_path"] = self._form_label("Команда:", other_col)
        row13.addWidget(self.llm_provider_labels["other_path"])
        self.entry_llm_other_path = QLineEdit()
        self.entry_llm_other_path.setPlaceholderText("путь к CLI, например my-llm")
        row13.addWidget(self.entry_llm_other_path, 1)
        other_layout.addLayout(row13)
        row14 = QHBoxLayout()
        self.llm_provider_labels["other_args"] = self._form_label("Аргументы:", other_col)
        row14.addWidget(self.llm_provider_labels["other_args"])
        self.entry_llm_other_args = QLineEdit()
        self.entry_llm_other_args.setPlaceholderText("аргументы; промпт будет добавлен в конец как последний параметр")
        row14.addWidget(self.entry_llm_other_args, 1)
        other_layout.addLayout(row14)
        layout.addWidget(self.llm_other_settings_widget)

        self.lbl_llm_provider_info = QLabel()
        self.lbl_llm_provider_info.setWordWrap(True)
        self.lbl_llm_provider_info.setStyleSheet(self._transparent_label_style(self._colors()["text_mute2"], font_pt=9))
        layout.addWidget(self.lbl_llm_provider_info)

        self.combo_llm_provider.currentTextChanged.connect(self._update_llm_provider_fields)
        self._update_llm_provider_fields(self.combo_llm_provider.currentText())
        group.setLayout(layout)
        return group

    def _update_llm_provider_fields(self, provider: str):
        provider = self._normalize_llm_provider(provider)
        widgets = {
            "API": self.llm_api_settings_widget,
            "Claude Code": self.llm_claude_settings_widget,
            "Codex": self.llm_codex_settings_widget,
            "OpenCode": self.llm_opencode_settings_widget,
            "Pi": self.llm_pi_settings_widget,
            "Other": self.llm_other_settings_widget,
        }
        for name, widget in widgets.items():
            widget.setVisible(name == provider)

        info_map = {
            "API": self._t("Режим автоопределения API: поддерживает OpenAI-compatible и Anthropic Messages API. localhost/local network тоже поддерживается, если сервер совместим с одним из этих форматов.", "API auto-detection mode: supports OpenAI-compatible APIs and the Anthropic Messages API. localhost/local network is also supported if the server is compatible with one of these formats."),
            "Claude Code": self._t("Локальный Claude CLI. Используются путь к claude, модель и доп. аргументы.", "Local Claude CLI. Uses the claude path, model, and extra arguments."),
            "Codex": self._t("Локальный Codex CLI. Используются путь к codex, модель и доп. аргументы.", "Local Codex CLI. Uses the codex path, model, and extra arguments."),
            "OpenCode": self._t("Локальный OpenCode CLI. Будет запущен как команда + аргументы + промпт в конце.", "Local OpenCode CLI. It will be launched as command + arguments + prompt at the end."),
            "Pi": self._t("Локальный pi CLI. Можно указать внутренний provider для pi, модель и доп. аргументы.", "Local pi CLI. You can specify the internal provider for pi, the model, and extra arguments."),
            "Other": self._t("Произвольный CLI. Укажи команду и аргументы; промпт будет передан последним аргументом.", "Arbitrary CLI. Specify the command and arguments; the prompt will be passed as the last argument."),
        }
        self.lbl_llm_provider_info.setText(info_map.get(provider, ""))

    def _ensure_llm_settings_dialog(self):
        if getattr(self, "_llm_settings_dialog", None) is not None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Настройки LLM")
        dialog.setMinimumWidth(self._px(760))
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(self._px(12), self._px(12), self._px(12), self._px(12))
        layout.setSpacing(self._px(8))
        self.grp_llm_api_settings = self._create_llm_api_group()
        layout.addWidget(self.grp_llm_api_settings)

        self.prompts_group = QGroupBox("Готовые промпты")
        prompts_layout = QVBoxLayout()
        prompts_layout.setContentsMargins(self._px(12), self._px(8), self._px(12), self._px(8))
        prompts_layout.setSpacing(self._px(8))

        self.lbl_llm_summary_prompt = QLabel("Промпт для выжимки:")
        prompts_layout.addWidget(self.lbl_llm_summary_prompt)
        self.txt_llm_summary_prompt = QTextEdit()
        self.txt_llm_summary_prompt.setMinimumHeight(self._px(100))
        prompts_layout.addWidget(self.txt_llm_summary_prompt)

        self.lbl_llm_tasks_prompt = QLabel("Промпт для задач:")
        prompts_layout.addWidget(self.lbl_llm_tasks_prompt)
        self.txt_llm_tasks_prompt = QTextEdit()
        self.txt_llm_tasks_prompt.setMinimumHeight(self._px(100))
        prompts_layout.addWidget(self.txt_llm_tasks_prompt)
        self.txt_llm_custom_prompt = QTextEdit()
        self.prompts_group.setLayout(prompts_layout)
        layout.addWidget(self.prompts_group)

        self.lbl_llm_settings_note = QLabel("Можно использовать OpenAI-compatible API, Anthropic Messages API, а также локальные Claude Code / Codex / OpenCode / Pi. Для API режим сам определяет тип API по URL или endpoint. Выбранный провайдер, модель, temperature, чекбоксы, prompt и файлы сохраняются между запусками. API Key лучше хранить в .env.")
        self.lbl_llm_settings_note.setWordWrap(True)
        self.lbl_llm_settings_note.setContentsMargins(self._px(4), self._px(2), self._px(4), self._px(2))
        self.lbl_llm_settings_note.setStyleSheet(self._transparent_label_style(self._colors()["text_mute2"], font_pt=9))
        layout.addWidget(self.lbl_llm_settings_note)

        self._llm_settings_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close)
        self._llm_settings_buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        self._llm_settings_buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        self._llm_settings_buttons.accepted.connect(self._save_llm_settings_from_dialog)
        self._llm_settings_buttons.rejected.connect(dialog.reject)
        self._llm_settings_buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(dialog.accept)
        layout.addWidget(self._llm_settings_buttons)
        self._llm_settings_dialog = dialog

    def _save_llm_settings_from_dialog(self):
        try:
            self._collect_llm_settings()
        except ValueError as e:
            QMessageBox.warning(self, self._t("Внимание", "Attention"), str(e))
            return
        self._save_ui_settings()
        QMessageBox.information(self, self._t("Настройки", "Settings"), self._t("LLM-настройки сохранены", "LLM settings saved"))

    def _open_llm_settings_dialog(self):
        self._ensure_llm_settings_dialog()
        self._llm_settings_dialog.exec()

    def _create_llm_actions_group(self) -> QGroupBox:
        group = QGroupBox("3. Что сделать")
        self.grp_llm_actions = group
        layout = QVBoxLayout()
        layout.setContentsMargins(self._px(12), self._px(8), self._px(12), self._px(8))
        layout.setSpacing(self._px(6))
        self.llm_action_checkboxes = {}

        row = QHBoxLayout()
        row.setSpacing(self._px(20))
        for key, label, checked in (
            ("summary", "Выжимка", True),
            ("tasks", "Задачи", False),
            ("custom", "Свой промпт", False),
        ):
            cb = QCheckBox(label)
            cb.setChecked(checked)
            row.addWidget(cb)
            self.llm_action_checkboxes[key] = cb
        row.addStretch()
        layout.addLayout(row)

        self.lbl_llm_actions_note = QLabel("Отметьте один или несколько режимов обработки. Для «Свой промпт» текст задается в меню «Настройки → LLM API…».")
        self.lbl_llm_actions_note.setWordWrap(True)
        self.lbl_llm_actions_note.setStyleSheet(self._transparent_label_style(self._colors()["text_mute2"], font_pt=9))
        layout.addWidget(self.lbl_llm_actions_note)
        group.setLayout(layout)
        return group

    def _create_llm_output_group(self) -> QGroupBox:
        group = QGroupBox("2. Куда сохранить")
        self.grp_llm_output = group
        layout = QVBoxLayout()
        layout.setContentsMargins(self._px(12), self._px(8), self._px(12), self._px(8))
        layout.setSpacing(self._px(6))

        row = QHBoxLayout()
        row.setSpacing(self._px(10))
        self.btn_llm_output = QPushButton("Выбрать папку")
        btn_output = self.btn_llm_output
        btn_output.setFixedHeight(self._px(36))
        btn_output.clicked.connect(self._select_llm_output_folder)
        row.addWidget(btn_output)
        self.lbl_llm_output = QLabel("Папка не выбрана (по умолчанию - рядом с транскриптом)")
        self.lbl_llm_output.setStyleSheet(self._transparent_label_style(self._colors()["text_mute"]))
        row.addWidget(self.lbl_llm_output, 1)
        layout.addLayout(row)

        self.lbl_llm_output_note = QLabel("Если папка не выбрана, результат будет сохранен рядом с исходным транскриптом.")
        self.lbl_llm_output_note.setWordWrap(True)
        self.lbl_llm_output_note.setStyleSheet(self._transparent_label_style(self._colors()["text_mute2"], font_pt=9))
        layout.addWidget(self.lbl_llm_output_note)
        group.setLayout(layout)
        return group

    def _create_llm_save_group(self) -> QGroupBox:
        group = QGroupBox("4. Форматы вывода")
        self.grp_llm_save = group
        layout = QVBoxLayout()
        layout.setContentsMargins(self._px(12), self._px(8), self._px(12), self._px(8))
        layout.setSpacing(self._px(6))
        self.llm_export_checkboxes = {}

        row1 = QHBoxLayout()
        row1.setSpacing(self._px(20))
        for key, label, checked in (("txt", "TXT (.txt)", True), ("md", "Markdown (.md)", False), ("docx", "DOCX (.docx)", False)):
            cb = QCheckBox(label)
            cb.setChecked(checked)
            row1.addWidget(cb)
            self.llm_export_checkboxes[key] = cb
        row1.addStretch()
        layout.addLayout(row1)
        group.setLayout(layout)
        return group

    def _create_llm_source_group(self) -> QGroupBox:
        group = QGroupBox("1. Источник транскрипта")
        self.grp_llm_source = group
        layout = QVBoxLayout()
        layout.setContentsMargins(self._px(12), self._px(8), self._px(12), self._px(8))
        layout.setSpacing(self._px(6))

        files_row = QHBoxLayout()
        self.btn_select_transcripts = QPushButton("Выбрать транскрипты")
        btn_select_transcripts = self.btn_select_transcripts
        btn_select_transcripts.setFixedHeight(self._px(36))
        btn_select_transcripts.clicked.connect(self._select_llm_transcript_files)
        files_row.addWidget(btn_select_transcripts)
        files_row.addStretch()
        layout.addLayout(files_row)
        # Совместимость с кодом, обращающимся к self.lbl_llm_files напрямую в lbl_llm_files_count
        # (единая видимая метка статуса вместо двух дублирующих надписей).

        self.llm_drop_hint = QLabel("Перетащите сюда транскрипты  ·  либо нажмите «Выбрать транскрипты»")
        self.llm_drop_hint.setObjectName("llm_drop_hint")
        self.llm_drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.llm_drop_hint.setMinimumHeight(self._px(50))
        layout.addWidget(self.llm_drop_hint)

        self.llm_files_list = QListWidget()
        self.llm_files_list.setObjectName("llm_files_list")
        self.llm_files_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.llm_files_list.setMinimumHeight(self._px(72))
        self.llm_files_list.setMaximumHeight(self._px(150))
        self.llm_files_list.setToolTip("Список транскриптов. Выделите и нажмите Delete, чтобы убрать.")
        self.llm_files_list.itemSelectionChanged.connect(self._update_llm_files_controls)
        self.llm_files_list.setVisible(False)
        layout.addWidget(self.llm_files_list)

        controls = QHBoxLayout()
        controls.setSpacing(self._px(10))
        self.lbl_llm_files_count = QLabel("Файлы не выбраны")
        self.lbl_llm_files_count.setStyleSheet(self._transparent_label_style(self._colors()["text_mute"]))
        controls.addWidget(self.lbl_llm_files_count)
        self.lbl_llm_files = self.lbl_llm_files_count
        controls.addStretch()
        self.btn_remove_llm_file = QPushButton("Убрать выбранное")
        self.btn_remove_llm_file.setFixedHeight(self._px(32))
        self.btn_remove_llm_file.setEnabled(False)
        self.btn_remove_llm_file.clicked.connect(self._remove_selected_llm_files)
        controls.addWidget(self.btn_remove_llm_file)
        self.btn_clear_llm_files = QPushButton("Очистить список")
        self.btn_clear_llm_files.setFixedHeight(self._px(32))
        self.btn_clear_llm_files.setEnabled(False)
        self.btn_clear_llm_files.clicked.connect(self._clear_llm_files_list)
        controls.addWidget(self.btn_clear_llm_files)
        layout.addLayout(controls)

        self.lbl_llm_supported = QLabel("Поддерживаемые файлы: .txt, .md, .srt, .vtt — либо вставьте транскрипт вручную ниже")
        info = self.lbl_llm_supported
        info.setStyleSheet(self._transparent_label_style(self._colors()["text_mute2"], font_pt=9))
        layout.addWidget(info)

        self.txt_llm_transcript = QTextEdit()
        self.txt_llm_transcript.setPlaceholderText("Вставьте сюда транскрипт, если не хотите выбирать файлы")
        self.txt_llm_transcript.setMinimumHeight(self._px(170))
        layout.addWidget(self.txt_llm_transcript)
        group.setLayout(layout)
        return group

    def _create_llm_prompt_group(self) -> QGroupBox:
        group = QGroupBox("2. Промпт")
        layout = QVBoxLayout()
        layout.setContentsMargins(self._px(12), self._px(8), self._px(12), self._px(9))
        layout.setSpacing(self._px(6))

        hint = QLabel("Все промпты настраиваются в меню «Настройки → LLM API…». Здесь достаточно выбрать режимы обработки. Для режима «Свой промпт» заранее заполните пользовательский промпт в настройках.")
        hint.setWordWrap(True)
        hint.setStyleSheet(self._transparent_label_style(self._colors()["text_mute2"], font_pt=9))
        layout.addWidget(hint)
        group.setLayout(layout)
        return group

    def _create_llm_result_group(self) -> QGroupBox:
        group = QGroupBox("5. Результат LLM")
        self.grp_llm_result = group
        layout = QVBoxLayout()
        layout.setContentsMargins(self._px(12), self._px(8), self._px(12), self._px(8))
        layout.setSpacing(self._px(6))

        self.lbl_llm_status = QLabel("Готово к LLM-обработке")
        self.lbl_llm_status.setWordWrap(True)
        self.lbl_llm_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_llm_status.setStyleSheet(
            self._transparent_label_style(self._colors()["text_sub"], font_pt=10, font_weight="bold")
        )
        layout.addWidget(self.lbl_llm_status)

        self.txt_llm_result = QTextEdit()
        self.txt_llm_result.setReadOnly(True)
        self.txt_llm_result.setFont(self._font(10, fixed=True))
        self.txt_llm_result.setMinimumHeight(self._px(260))
        layout.addWidget(self.txt_llm_result, 1)
        group.setLayout(layout)
        return group

    # ──────────────────────────────────────────────────────────────
    # Диалог HF токена
    # ──────────────────────────────────────────────────────────────
