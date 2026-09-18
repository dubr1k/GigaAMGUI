"""LLM-tab widget construction for the desktop application."""
from __future__ import annotations

import os
import threading

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
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

    # ── диалог: API, инструменты, аргументы ─────────────────────────────

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
        self.cb_llm_allow_tools.setToolTip(
            "Выключено: CLI запускается без инструментов и без сохранения сессии (--no-tools/--no-session и аналоги)."
        )
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

    def _create_llm_tools_table(self) -> QGroupBox:
        """Таблица найденных CLI: статус · инструмент · версия · путь · Обзор · Проверить."""
        group = QGroupBox("Инструменты")
        group.setObjectName("llm_tools_group")
        self.grp_llm_tools = group
        box = QVBoxLayout()
        box.setContentsMargins(self._px(8), self._px(6), self._px(8), self._px(6))
        box.setSpacing(self._px(6))

        specs = cli_tools.cli_specs()
        table = QTableWidget(len(specs), 6)
        table.setObjectName("llm_tools_table")
        self.tbl_llm_tools = table
        table.setHorizontalHeaderLabels(["", "Инструмент", "Версия", "Путь", "", ""])
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        table.verticalHeader().setDefaultSectionSize(self._px(30))
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        table.setShowGrid(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        # Кнопки — cellWidget'ы, ResizeToContents их не видит: ширина по sizeHint самой кнопки.
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self._llm_tool_rows: dict[str, int] = {}
        self._llm_tool_buttons: dict[str, tuple[QPushButton, QPushButton]] = {}
        for row, spec in enumerate(specs):
            self._llm_tool_rows[spec.name] = row
            status_item = QTableWidgetItem("…")
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 0, status_item)
            table.setItem(row, 1, QTableWidgetItem(spec.name))
            table.setItem(row, 2, QTableWidgetItem(""))
            path_entry = QLineEdit()
            path_entry.setObjectName("llm_tool_path")
            path_entry.setPlaceholderText(spec.binary)
            path_entry.setToolTip("Пусто — искать автоматически (PATH + типичные каталоги установки)")
            path_entry.editingFinished.connect(lambda name=spec.name: self._on_llm_tool_path_edited(name))
            setattr(self, f"entry_llm_{spec.settings_prefix}_path", path_entry)
            table.setCellWidget(row, 3, path_entry)
            browse = QPushButton("Обзор…")
            browse.setObjectName("llm_tool_browse")
            browse.clicked.connect(lambda _=False, name=spec.name: self._browse_llm_tool(name))
            table.setCellWidget(row, 4, browse)
            check = QPushButton("Проверить")
            check.setObjectName("llm_tool_check")
            check.clicked.connect(lambda _=False, name=spec.name: self._check_llm_tool(name))
            table.setCellWidget(row, 5, check)
            self._llm_tool_buttons[spec.name] = (browse, check)
        button_width = max(b.sizeHint().width() for pair in self._llm_tool_buttons.values() for b in pair) + self._px(8)
        table.setColumnWidth(4, button_width)
        table.setColumnWidth(5, button_width)
        # Все строки видны целиком — таблица не скроллится, её высота фиксирована по числу провайдеров.
        row_height = table.verticalHeader().defaultSectionSize()
        header_height = max(header.sizeHint().height(), row_height)
        table.setFixedHeight(header_height + len(specs) * row_height + 2 * table.frameWidth() + self._px(4))
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        box.addWidget(table)

        footer = QHBoxLayout()
        self.lbl_llm_tools_note = QLabel("Пустой путь — автопоиск по PATH и типичным каталогам (homebrew, npm, bun, nvm).")
        self.lbl_llm_tools_note.setWordWrap(True)
        self.lbl_llm_tools_note.setStyleSheet(self._transparent_label_style(self._colors()["text_mute2"], font_pt=9))
        footer.addWidget(self.lbl_llm_tools_note, 1)
        self.btn_llm_tools_rescan = QPushButton("Пересканировать")
        self.btn_llm_tools_rescan.setObjectName("llm_tools_rescan")
        self.btn_llm_tools_rescan.clicked.connect(lambda: self._refresh_llm_tools(fresh=True))
        footer.addWidget(self.btn_llm_tools_rescan)
        box.addLayout(footer)
        group.setLayout(box)
        return group

    # ── статусы инструментов ────────────────────────────────────────────

    def _llm_tool_overrides(self) -> dict:
        overrides = {}
        for spec in cli_tools.cli_specs():
            entry = getattr(self, f"entry_llm_{spec.settings_prefix}_path", None)
            value = entry.text().strip() if entry is not None else ""
            if value and value != spec.binary:
                overrides[spec.id] = value
        return overrides

    def _refresh_llm_tools(self, fresh: bool = False):
        """Скан в фоне; результат приходит сигналом llm_tools_scanned."""
        if getattr(self, "_llm_scan_running", False):
            return
        self._llm_scan_running = True
        if hasattr(self, "btn_llm_tools_rescan"):
            self.btn_llm_tools_rescan.setEnabled(False)
        overrides = self._llm_tool_overrides()
        signals = self.signals

        def work():
            try:
                statuses = cli_tools.scan(overrides, fresh=fresh)
            except Exception as exc:  # noqa: BLE001 — статус-строка, не падение UI
                statuses = exc
            signals.llm_tools_scanned.emit(statuses)

        threading.Thread(target=work, name="llm-tools-scan", daemon=True).start()

    def _on_llm_tools_scanned(self, statuses):
        self._llm_scan_running = False
        if hasattr(self, "btn_llm_tools_rescan"):
            self.btn_llm_tools_rescan.setEnabled(True)
        if isinstance(statuses, Exception):
            self.log(f"LLM: не удалось просканировать CLI-инструменты: {statuses}")
            return
        for status in statuses:
            self._llm_tool_statuses[status.provider] = status
        self._render_llm_tool_statuses()

    def _check_llm_tool(self, provider_name: str):
        spec = cli_tools.provider_by_name(provider_name)
        entry = getattr(self, f"entry_llm_{spec.settings_prefix}_path")
        override = entry.text().strip() or None
        browse, check = self._llm_tool_buttons[provider_name]
        check.setEnabled(False)
        signals = self.signals

        def work():
            signals.llm_tool_checked.emit(cli_tools.resolve_tool(spec, override))

        threading.Thread(target=work, name="llm-tool-check", daemon=True).start()

    def _on_llm_tool_checked(self, status):
        self._llm_tool_statuses[status.provider] = status
        if status.provider in self._llm_tool_buttons:
            self._llm_tool_buttons[status.provider][1].setEnabled(True)
        self._render_llm_tool_statuses()

    def _on_llm_tool_path_edited(self, provider_name: str):
        # Правка пути делает старый статус недостоверным — перепроверяем именно этот инструмент.
        self._check_llm_tool(provider_name)

    def _browse_llm_tool(self, provider_name: str):
        spec = cli_tools.provider_by_name(provider_name)
        entry = getattr(self, f"entry_llm_{spec.settings_prefix}_path")
        start = entry.text().strip() or os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(
            self, self._t(f"Путь к {spec.name}", f"{spec.name} executable"), start,
        )
        if path:
            entry.setText(path)
            self._check_llm_tool(provider_name)

    _LLM_STATUS_GLYPH = {"found": "●", "missing": "○", "broken": "⚠"}

    def _llm_status_color(self, status: str) -> QColor:
        colors = self._colors()
        if status == "found":
            return QColor(colors.get("success", "#3fb950"))
        if status == "broken":
            return QColor(colors.get("warning", "#e3b341"))
        return QColor(colors.get("text_mute2", "#8b949e"))

    def _llm_status_icon(self, status: str) -> QIcon:
        size = self._px(10)
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._llm_status_color(status))
        painter.drawEllipse(1, 1, size - 2, size - 2)
        painter.end()
        return QIcon(pixmap)

    def _llm_status_text(self, status) -> str:
        if status is None:
            return ""
        if status.status == "found":
            return status.version or self._t("найден", "found")
        if status.status == "broken":
            return self._t("не запускается", "does not run")
        return self._t("не найден", "not found")

    def _render_llm_tool_statuses(self):
        """Таблица, иконки в комбо и бейдж на странице — из self._llm_tool_statuses."""
        table = getattr(self, "tbl_llm_tools", None)
        for name, status in self._llm_tool_statuses.items():
            if table is not None and name in self._llm_tool_rows:
                row = self._llm_tool_rows[name]
                item = table.item(row, 0)
                item.setText(self._LLM_STATUS_GLYPH.get(status.status, "○"))
                item.setForeground(self._llm_status_color(status.status))
                detail = status.detail or (status.install_hint and f"{self._t('Установка', 'Install')}: {status.install_hint}") or ""
                item.setToolTip(detail)
                table.item(row, 2).setText(self._llm_status_text(status))
                table.item(row, 2).setToolTip(detail)
                entry = table.cellWidget(row, 3)
                if entry is not None:
                    hint = status.path if status.status != "missing" else f"{status.provider}: {self._t('не найден', 'not found')}"
                    entry.setToolTip(hint or "")
                    if not entry.text().strip():
                        entry.setPlaceholderText(status.path or cli_tools.provider_by_name(name).binary)
            index = self.combo_llm_provider.findData(name)
            if index >= 0:
                self.combo_llm_provider.setItemIcon(index, self._llm_status_icon(status.status))
                self.combo_llm_provider.setItemData(
                    index,
                    self._llm_status_text(status) + (f" · {status.path}" if status.path else ""),
                    Qt.ItemDataRole.ToolTipRole,
                )
                if status.status == "missing":
                    self.combo_llm_provider.setItemData(index, self._llm_status_color("missing"), Qt.ItemDataRole.ForegroundRole)
                else:
                    self.combo_llm_provider.setItemData(index, None, Qt.ItemDataRole.ForegroundRole)
        self._update_llm_provider_status_label()

    def _update_llm_provider_status_label(self):
        label = getattr(self, "lbl_llm_provider_status", None)
        if label is None:
            return
        provider = self._normalize_llm_provider(self.combo_llm_provider.currentText())
        status = self._llm_tool_statuses.get(provider)
        colors = self._colors()
        if provider == "API":
            text, color = self._t("HTTP API", "HTTP API"), colors["text_mute2"]
        elif provider == "Other":
            text, color = self._t("произвольная команда", "custom command"), colors["text_mute2"]
        elif status is None:
            text, color = self._t("проверка…", "checking…"), colors["text_mute2"]
        elif status.status == "found":
            text = f"{self._LLM_STATUS_GLYPH['found']} {status.version or self._t('найден', 'found')}"
            color = self._llm_status_color("found").name()
            label.setToolTip(status.path or "")
        elif status.status == "broken":
            text = f"{self._LLM_STATUS_GLYPH['broken']} {self._t('не запускается', 'does not run')}"
            color = self._llm_status_color("broken").name()
            label.setToolTip(status.detail or "")
        else:
            text = f"{self._LLM_STATUS_GLYPH['missing']} {self._t('не найден', 'not found')}"
            color = self._llm_status_color("missing").name()
            label.setToolTip(f"{self._t('Установка', 'Install')}: {status.install_hint}" if status.install_hint else "")
        label.setText(text)
        label.setStyleSheet(self._transparent_label_style(color, font_pt=9))

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
        self.txt_llm_custom_prompt.setPlaceholderText(
            "Текст для режима «Свой промпт». Транскрипт будет добавлен ниже автоматически."
        )
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

    # ──────────────────────────────────────────────────────────────
    # Диалог HF токена
    # ──────────────────────────────────────────────────────────────
