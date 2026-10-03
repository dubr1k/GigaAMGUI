"""Таблица CLI-инструментов LLM и их статусы (найден / не найден / не запускается).

Сканирование и проверка идут в фоне (cli_tools.scan / resolve_tool), результат
приходит сигналами; статусы рисуются в таблице диалога настроек, иконками в
комбо провайдера и бейджем на странице LLM.

Mixin: методы работают со `self` главного окна.
"""
from __future__ import annotations

import os
import threading

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..services import cli_tools


class LlmToolsMixin:
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
            self._bilingual(path_entry.setToolTip, "Пусто — искать автоматически (PATH + типичные каталоги установки)", "Empty — find automatically (PATH + common install folders)")
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
            try:
                signals.llm_tools_scanned.emit(statuses)
            except RuntimeError:
                pass  # окно закрыли, пока шёл скан (--version у каждого CLI)

        threading.Thread(target=work, name="llm-tools-scan", daemon=True).start()

    def _on_llm_tools_scanned(self, statuses):
        self._llm_scan_running = False
        if hasattr(self, "btn_llm_tools_rescan"):
            self.btn_llm_tools_rescan.setEnabled(True)
        if isinstance(statuses, Exception):
            self.log(self._t(
                f"LLM: не удалось просканировать CLI-инструменты: {statuses}",
                f"LLM: could not scan the CLI tools: {statuses}",
            ))
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
            status = cli_tools.resolve_tool(spec, override)
            try:
                signals.llm_tool_checked.emit(status)
            except RuntimeError:
                pass  # окно закрыли, пока шла проверка

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
