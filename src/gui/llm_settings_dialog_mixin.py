"""Диалог «Настройки LLM»: API, аргументы CLI-провайдеров и готовые промпты.

Комбо провайдера и поле модели живут на странице LLM (LlmUiMixin); диалог
показывает только поля выбранного провайдера.

Mixin: методы работают со `self` главного окна.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..services import cli_tools


class LlmSettingsDialogMixin:
    def _create_llm_api_group(self) -> QGroupBox:
        self._ensure_llm_provider_widgets()
        group = QGroupBox("LLM API")
        layout = QVBoxLayout()
        layout.setContentsMargins(self._px(12), self._px(8), self._px(12), self._px(10))
        layout.setSpacing(self._px(8))

        label_col = self._label_column_width(("Temperature:", "API URL:", "API Key:"))

        common_row = QHBoxLayout()
        self.llm_provider_labels["temperature"] = self._form_label("Temperature:", label_col)
        common_row.addWidget(self.llm_provider_labels["temperature"])
        self.entry_llm_temperature = QLineEdit()
        self.entry_llm_temperature.setMaximumWidth(self._px(110))
        common_row.addWidget(self.entry_llm_temperature)
        common_row.addStretch()
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

        layout.addWidget(self._create_llm_tools_table())

        # Аргументы/provider выбранного CLI и команда для «Другое».
        self.llm_cli_settings_widgets = {}
        for spec in cli_tools.cli_specs():
            self.llm_cli_settings_widgets[spec.name] = self._create_llm_cli_args_widget(spec)
            layout.addWidget(self.llm_cli_settings_widgets[spec.name])
        self.llm_claude_settings_widget = self.llm_cli_settings_widgets["Claude Code"]
        self.llm_codex_settings_widget = self.llm_cli_settings_widgets["Codex"]
        self.llm_opencode_settings_widget = self.llm_cli_settings_widgets["OpenCode"]
        self.llm_pi_settings_widget = self.llm_cli_settings_widgets["Pi"]
        self.llm_omp_settings_widget = self.llm_cli_settings_widgets["oh-my-pi"]

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
        self.entry_llm_other_args.setPlaceholderText("аргументы; промпт — последним параметром, либо {stdin}")
        row14.addWidget(self.entry_llm_other_args, 1)
        other_layout.addLayout(row14)
        layout.addWidget(self.llm_other_settings_widget)

        self.cb_llm_allow_tools = QCheckBox("Разрешить инструменты и сессии агента")
        self.cb_llm_allow_tools.setObjectName("llm_allow_tools")
        self._bilingual(self.cb_llm_allow_tools.setToolTip, "Выключено: CLI запускается без инструментов и без сохранения сессии (--no-tools/--no-session и аналоги).", "Off: the CLI runs without tools and without saving a session (--no-tools/--no-session or equivalents).")
        layout.addWidget(self.cb_llm_allow_tools)

        self.lbl_llm_provider_info = QLabel()
        self.lbl_llm_provider_info.setWordWrap(True)
        self.lbl_llm_provider_info.setStyleSheet(self._transparent_label_style(self._colors()["text_mute2"], font_pt=9))
        layout.addWidget(self.lbl_llm_provider_info)

        self.combo_llm_provider.currentTextChanged.connect(self._update_llm_provider_fields)
        self._update_llm_provider_fields(self.combo_llm_provider.currentText())
        group.setLayout(layout)
        return group

    def _create_llm_cli_args_widget(self, spec) -> QWidget:
        """Аргументы (и внутренний provider для pi/omp) одного CLI-провайдера."""
        widget = QWidget()
        widget.setStyleSheet("background: transparent;")
        box = QVBoxLayout(widget)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(self._px(8))
        labels = [f"{spec.name} доп. аргументы:"] + ([f"{spec.name} provider:"] if spec.has_provider_field else [])
        col = self._label_column_width(tuple(labels))
        if spec.has_provider_field:
            row = QHBoxLayout()
            self.llm_provider_labels[f"{spec.settings_prefix}_provider"] = self._form_label(f"{spec.name} provider:", col)
            row.addWidget(self.llm_provider_labels[f"{spec.settings_prefix}_provider"])
            entry = QLineEdit()
            entry.setPlaceholderText("anthropic / openai / google ...")
            setattr(self, f"entry_llm_{spec.settings_prefix}_provider", entry)
            row.addWidget(entry, 1)
            box.addLayout(row)
        row = QHBoxLayout()
        self.llm_provider_labels[f"{spec.settings_prefix}_args"] = self._form_label(f"{spec.name} доп. аргументы:", col)
        row.addWidget(self.llm_provider_labels[f"{spec.settings_prefix}_args"])
        entry = QLineEdit()
        entry.setPlaceholderText({
            "claude": "например: --permission-mode bypassPermissions",
            "codex": "например: --dangerously-bypass-approvals-and-sandbox",
            "opencode": "например: --agent build",
            "pi": "например: --thinking low",
            "omp": "например: --thinking low --profile work",
        }.get(spec.id, ""))
        setattr(self, f"entry_llm_{spec.settings_prefix}_args", entry)
        row.addWidget(entry, 1)
        box.addLayout(row)
        return widget

    def _update_llm_provider_fields(self, provider: str):
        provider = self._normalize_llm_provider(provider)
        self.llm_api_settings_widget.setVisible(provider == "API")
        for name, widget in self.llm_cli_settings_widgets.items():
            widget.setVisible(name == provider)
        self.llm_other_settings_widget.setVisible(provider == "Other")
        self.cb_llm_allow_tools.setVisible(provider not in ("API", "Other"))
        self.grp_llm_tools.setVisible(provider != "API")

        info_map = {
            "API": self._t("Режим автоопределения API: поддерживает OpenAI-compatible и Anthropic Messages API. localhost/local network тоже поддерживается, если сервер совместим с одним из этих форматов.", "API auto-detection mode: supports OpenAI-compatible APIs and the Anthropic Messages API. localhost/local network is also supported if the server is compatible with one of these formats."),
            "Claude Code": self._t("Локальный Claude CLI (claude -p). Промпт передаётся через stdin; модель и доп. аргументы — из настроек.", "Local Claude CLI (claude -p). The prompt goes through stdin; model and extra arguments come from the settings."),
            "Codex": self._t("Локальный Codex CLI (codex exec). Модель выбирает сам клиент Codex.", "Local Codex CLI (codex exec). The Codex client picks the model itself."),
            "OpenCode": self._t("Локальный OpenCode CLI (opencode run). Модель в формате provider/model.", "Local OpenCode CLI (opencode run). Model in provider/model format."),
            "Pi": self._t("Локальный pi CLI (pi -p). Можно указать внутренний provider, модель и доп. аргументы.", "Local pi CLI (pi -p). You can specify the internal provider, the model, and extra arguments."),
            "oh-my-pi": self._t("oh-my-pi (omp -p) — форк pi с 60+ провайдерами. Модель задаётся нечётко: «opus», «gpt-5.2» или «openai/gpt-5.2».", "oh-my-pi (omp -p) — a pi fork with 60+ providers. Model is fuzzy-matched: “opus”, “gpt-5.2” or “openai/gpt-5.2”."),
            "Other": self._t("Произвольный CLI. Промпт передаётся последним аргументом и в stdin; напишите {stdin} в аргументах, чтобы передавать только через stdin.", "Arbitrary CLI. The prompt is passed as the last argument and via stdin; put {stdin} in the arguments to pass it via stdin only."),
        }
        self.lbl_llm_provider_info.setText(info_map.get(provider, ""))
        self._update_llm_provider_status_label()

    def _ensure_llm_settings_dialog(self):
        if getattr(self, "_llm_settings_dialog", None) is not None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Настройки LLM")
        dialog.setMinimumWidth(self._px(760))
        # Содержимое выше маленького экрана (таблица + три промпта): скролл, а не сжатие виджетов внахлёст.
        outer = QVBoxLayout(dialog)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea()
        scroll.setObjectName("llm_settings_scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("llm_settings_body")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(self._px(12), self._px(12), self._px(12), self._px(12))
        layout.setSpacing(self._px(8))
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
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

        self.lbl_llm_custom_prompt = QLabel("Свой промпт:")
        prompts_layout.addWidget(self.lbl_llm_custom_prompt)
        self.txt_llm_custom_prompt = QTextEdit()
        self.txt_llm_custom_prompt.setMinimumHeight(self._px(100))
        self._bilingual(self.txt_llm_custom_prompt.setPlaceholderText, "Текст для режима «Свой промпт». Транскрипт будет добавлен ниже автоматически.", "Text for the “Custom prompt” mode. The transcript is appended below automatically.")
        prompts_layout.addWidget(self.txt_llm_custom_prompt)
        self.prompts_group.setLayout(prompts_layout)
        layout.addWidget(self.prompts_group)

        self.lbl_llm_settings_note = QLabel("Можно использовать OpenAI-compatible API, Anthropic Messages API, а также локальные Claude Code / Codex / OpenCode / Pi / oh-my-pi. Для API режим сам определяет тип API по URL или endpoint. Выбранный провайдер, модель, temperature, чекбоксы, prompt и файлы сохраняются между запусками. API Key лучше хранить в .env.")
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
        buttons_row = QWidget()
        buttons_layout = QVBoxLayout(buttons_row)
        buttons_layout.setContentsMargins(self._px(12), self._px(6), self._px(12), self._px(10))
        buttons_layout.addWidget(self._llm_settings_buttons)
        outer.addWidget(buttons_row)
        dialog.resize(self._px(780), min(self._px(720), max(self._px(420), self._available_dialog_height())))
        self._llm_settings_dialog = dialog

    def _available_dialog_height(self) -> int:
        screen = self.screen() if hasattr(self, "screen") else None
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return self._px(720)
        return int(screen.availableGeometry().height() * 0.9)

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
        # Провайдер, модель, URL и temperature есть и на вкладке «Настройки».
        self._sync_support_surface_settings()

    def _retranslate_llm_settings_dialog(self, is_ru: bool) -> None:
        """Диалог «Настройки LLM»."""
        if not hasattr(self, "_llm_settings_dialog"):
            return
        self._llm_settings_dialog.setWindowTitle("Настройки LLM" if is_ru else "LLM settings")
        self.grp_llm_api_settings.setTitle("LLM API")
        self.llm_provider_labels["provider"].setText("Провайдер:" if is_ru else "Provider:")
        self.llm_provider_labels["model"].setText("Модель:" if is_ru else "Model:")
        other_index = self._llm_other_index
        self.llm_provider_items[other_index] = "Другое" if is_ru else "Other"
        current_provider = self._normalize_llm_provider(self.combo_llm_provider.currentText())
        self.combo_llm_provider.blockSignals(True)
        self.combo_llm_provider.setItemText(other_index, self.llm_provider_items[other_index])
        self.combo_llm_provider.setCurrentText(self.llm_provider_items[other_index] if current_provider == "Other" else current_provider)
        self.combo_llm_provider.blockSignals(False)
        self.grp_llm_tools.setTitle("Инструменты" if is_ru else "Tools")
        self.tbl_llm_tools.setHorizontalHeaderLabels(["", "Инструмент" if is_ru else "Tool", "Версия" if is_ru else "Version", "Путь" if is_ru else "Path", "", ""])
        for browse, check in self._llm_tool_buttons.values():
            browse.setText("Обзор…" if is_ru else "Browse…")
            check.setText("Проверить" if is_ru else "Check")
        self.btn_llm_tools_rescan.setText("Пересканировать" if is_ru else "Rescan")
        self.lbl_llm_tools_note.setText(
            "Пустой путь — автопоиск по PATH и типичным каталогам (homebrew, npm, bun, nvm)." if is_ru
            else "Empty path — auto-detect via PATH and common install folders (homebrew, npm, bun, nvm)."
        )
        self.cb_llm_allow_tools.setText("Разрешить инструменты и сессии агента" if is_ru else "Allow agent tools and sessions")
        for spec in cli_tools.cli_specs():
            prefix = spec.settings_prefix
            self.llm_provider_labels[f"{prefix}_args"].setText(f"{spec.name} доп. аргументы:" if is_ru else f"{spec.name} extra args:")
            if spec.has_provider_field:
                self.llm_provider_labels[f"{prefix}_provider"].setText(f"{spec.name} provider:")
        self.entry_llm_claude_args.setPlaceholderText("например: --permission-mode bypassPermissions" if is_ru else "example: --permission-mode bypassPermissions")
        self.entry_llm_codex_args.setPlaceholderText("например: --dangerously-bypass-approvals-and-sandbox" if is_ru else "example: --dangerously-bypass-approvals-and-sandbox")
        self.entry_llm_opencode_args.setPlaceholderText("например: --agent build" if is_ru else "example: --agent build")
        self.entry_llm_pi_args.setPlaceholderText("например: --thinking low" if is_ru else "example: --thinking low")
        self.entry_llm_omp_args.setPlaceholderText("например: --thinking low --profile work" if is_ru else "example: --thinking low --profile work")
        self.llm_provider_labels["other_path"].setText("Команда:" if is_ru else "Command:")
        self.llm_provider_labels["other_args"].setText("Аргументы:" if is_ru else "Arguments:")
        self.entry_llm_other_path.setPlaceholderText("путь к CLI, например my-llm" if is_ru else "CLI path, for example my-llm")
        self.entry_llm_other_args.setPlaceholderText("аргументы; промпт — последним параметром, либо {stdin}" if is_ru else "arguments; the prompt goes last, or write {stdin}")
        self._render_llm_tool_statuses()
        self.prompts_group.setTitle("Готовые промпты" if is_ru else "Ready prompts")
        self.lbl_llm_summary_prompt.setText("Промпт для выжимки:" if is_ru else "Prompt for summary:")
        self.lbl_llm_tasks_prompt.setText("Промпт для задач:" if is_ru else "Prompt for tasks:")
        self.lbl_llm_custom_prompt.setText("Свой промпт:" if is_ru else "Custom prompt:")
        self.lbl_llm_settings_note.setText("Можно использовать OpenAI-compatible API, Anthropic Messages API, а также локальные Claude Code / Codex / OpenCode / Pi / oh-my-pi. Для API режим сам определяет тип API по URL или endpoint. Выбранный провайдер, модель, temperature, чекбоксы, prompt и файлы сохраняются между запусками. API Key лучше хранить в .env." if is_ru else "You can use an OpenAI-compatible API, Anthropic Messages API, or local Claude Code / Codex / OpenCode / Pi / oh-my-pi. In API mode, the app auto-detects the API type from the URL or endpoint. The selected provider, model, temperature, checkboxes, prompts, and files are saved between launches. It is best to store the API key in .env.")
        self._llm_settings_buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить" if is_ru else "Save")
        self._llm_settings_buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть" if is_ru else "Close")
        self._update_llm_provider_fields(self.combo_llm_provider.currentText())
