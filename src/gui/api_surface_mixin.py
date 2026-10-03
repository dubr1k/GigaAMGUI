"""Вкладка «API»: статус отдельного API-сервиса, примеры запросов и документация.

Сам сервис (api.py) — отдельный процесс; desktop-приложение только проверяет
его /health и показывает, как к нему обращаться (OpenAI Audio API-совместимо).

Mixin: методы работают со `self` главного окна.
"""
from __future__ import annotations

import threading
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class ApiSurfaceMixin:
    """Build the desktop-only API documentation surface."""

    _API_DEFAULT_URL = "http://127.0.0.1:8000"

    def _create_api_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("api_page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(self._px(12), self._px(12), self._px(12), self._px(12))
        layout.setSpacing(self._px(10))

        heading = QLabel()
        self._bilingual(heading.setText, "API", "API")
        heading.setObjectName("support_heading")
        heading.setFont(self._font(18))
        layout.addWidget(heading)

        status_card = QFrame()
        status_card.setObjectName("api_status_bar")
        status_layout = QHBoxLayout(status_card)
        status_layout.setContentsMargins(self._px(12), self._px(9), self._px(12), self._px(9))
        status_layout.setSpacing(self._px(8))
        status_title = QLabel()
        self._bilingual(status_title.setText, "Статус API", "API status")
        status_title.setObjectName("api_status_title")
        status_title.setFont(self._font(10))
        status_layout.addWidget(status_title)
        self.api_status_label = QLabel()
        self.api_status_label.setObjectName("api_status")
        status_layout.addWidget(self.api_status_label)
        self.api_endpoint_input = QLineEdit(
            self.user_settings.get_value("api_base_url", self._API_DEFAULT_URL)
        )
        self.api_endpoint_input.setObjectName("api_endpoint_input")
        self.api_endpoint_input.setPlaceholderText(self._API_DEFAULT_URL)
        self.api_endpoint_input.setMinimumWidth(self._px(220))
        self.api_endpoint_input.editingFinished.connect(self._save_api_base_url)
        status_layout.addWidget(self.api_endpoint_input, 1)

        refresh = QPushButton()
        self._bilingual(refresh.setText, "Проверить", "Refresh")
        refresh.setObjectName("api_action_button")
        refresh.clicked.connect(self._refresh_api_status)
        status_layout.addWidget(refresh)
        copy_endpoint = QPushButton()
        self._bilingual(copy_endpoint.setText, "Скопировать", "Copy")
        copy_endpoint.setObjectName("api_action_button")
        copy_endpoint.clicked.connect(self._copy_api_endpoint)
        status_layout.addWidget(copy_endpoint)
        self.api_docs_button = QPushButton()
        self._bilingual(self.api_docs_button.setText, "Открыть документацию", "Open documentation")
        self.api_docs_button.setObjectName("api_primary_action")
        self.api_docs_button.clicked.connect(self._open_api_documentation)
        status_layout.addWidget(self.api_docs_button)
        layout.addWidget(status_card)

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setObjectName("api_surface_splitter")
        body.setChildrenCollapsible(False)
        body.addWidget(self._create_api_examples_panel())
        body.addWidget(self._create_api_documentation_panel())
        body.setSizes([self._px(590), self._px(420)])
        layout.addWidget(body, 1)

        self._refresh_api_status()
        return page

    def _create_api_examples_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("api_examples_panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(self._px(12), self._px(12), self._px(12), self._px(12))
        layout.setSpacing(self._px(8))

        title_row = QHBoxLayout()
        title = QLabel()
        self._bilingual(title.setText, "Примеры запросов", "Request examples")
        title.setObjectName("api_panel_title")
        title.setFont(self._font(11))
        title_row.addWidget(title)
        title_row.addStretch()
        copy_button = QPushButton()
        self._bilingual(copy_button.setText, "Копировать", "Copy")
        copy_button.setObjectName("api_action_button")
        copy_button.clicked.connect(self._copy_active_api_example)
        title_row.addWidget(copy_button)
        layout.addLayout(title_row)

        self.api_code_tabs = QTabWidget()
        self.api_code_tabs.setObjectName("api_code_tabs")
        self.api_code_edits = {}
        for language, code in self._api_examples().items():
            editor = QPlainTextEdit(code)
            editor.setObjectName("api_code_editor")
            editor.setReadOnly(True)
            editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
            editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            # Цвета — из темы (QPlainTextEdit#api_code_editor в общем QSS): прежний
            # встроенный светлый стиль оставался белым пятном в тёмной теме.
            self.api_code_edits[language] = editor
            self.api_code_tabs.addTab(editor, language)
        layout.addWidget(self.api_code_tabs, 1)
        return panel

    def _api_documentation_section(self, title: tuple[str, str], body: tuple[str, str], expanded: bool = False) -> QWidget:
        section = QFrame()
        section.setObjectName("api_doc_section")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(self._px(10), self._px(8), self._px(10), self._px(8))
        layout.setSpacing(self._px(5))
        toggle = QToolButton()
        toggle.setObjectName("api_doc_toggle")
        self._bilingual(toggle.setText, *title)
        toggle.setCheckable(True)
        toggle.setChecked(expanded)
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        layout.addWidget(toggle)
        content = QLabel()
        self._bilingual(content.setText, *body)
        content.setObjectName("api_doc_body")
        content.setWordWrap(True)
        content.setTextFormat(Qt.TextFormat.RichText)
        content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        content.setVisible(expanded)
        toggle.toggled.connect(content.setVisible)
        toggle.toggled.connect(
            lambda checked, button=toggle: button.setArrowType(
                Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
            )
        )
        layout.addWidget(content)
        return section

    def _create_api_documentation_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("api_documentation_panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(self._px(12), self._px(12), self._px(12), self._px(12))
        layout.setSpacing(self._px(7))

        title = QLabel()
        self._bilingual(title.setText, "Документация", "Documentation")
        title.setObjectName("api_panel_title")
        title.setFont(self._font(11))
        layout.addWidget(title)
        layout.addWidget(self._api_documentation_section(
            ("Быстрый старт", "Quick start"),
            (
                "Запустите отдельный API-сервис командой <code>python api.py</code>. "
                "Передавайте ключ в заголовке <code>Authorization: Bearer &lt;ключ&gt;</code>.",
                "Start the separate API service with <code>python api.py</code>. "
                "Send the key in the <code>Authorization: Bearer &lt;key&gt;</code> header.",
            ),
            expanded=True,
        ))
        layout.addWidget(self._api_documentation_section(
            ("Эндпоинты", "Endpoints"),
            (
                "<code>POST /v1/audio/transcriptions</code> — распознать файл (multipart: file, model, response_format).<br>"
                "<code>GET /v1/models</code> — доступные модели.<br>"
                "<code>GET /health</code> — состояние сервера.<br>"
                "Совместимо с OpenAI Audio API: base_url <code>http://127.0.0.1:8000/v1</code>, "
                "заголовок <code>Authorization: Bearer &lt;ключ&gt;</code>.",
                "<code>POST /v1/audio/transcriptions</code> — transcribe a file (multipart: file, model, response_format).<br>"
                "<code>GET /v1/models</code> — available models.<br>"
                "<code>GET /health</code> — server status.<br>"
                "OpenAI Audio API-compatible: base_url <code>http://127.0.0.1:8000/v1</code>, "
                "header <code>Authorization: Bearer &lt;key&gt;</code>.",
            ),
        ))
        layout.addWidget(self._api_documentation_section(
            ("Параметры и ответы", "Parameters and responses"),
            (
                "Загрузка принимает <code>file</code> и <code>model</code>; необязательные параметры: "
                "<code>response_format</code>, <code>stream</code>, <code>timestamp_granularities[]</code>, "
                "<code>diarize</code>, <code>diarization_backend</code>, <code>num_speakers</code>, "
                "<code>asr_backend</code>, <code>onnx_provider</code>, <code>audio_preprocessing</code>.<br><br>"
                "Ответ возвращается синхронно, в формате, заданном <code>response_format</code> "
                "(<code>json</code>/<code>text</code>/<code>srt</code>/<code>vtt</code>/<code>verbose_json</code>/<code>diarized_json</code>). "
                "Интерактивная схема доступна в <code>/docs</code>, когда сервис запущен.",
                "Uploads require <code>file</code> and <code>model</code>; optional parameters are "
                "<code>response_format</code>, <code>stream</code>, <code>timestamp_granularities[]</code>, "
                "<code>diarize</code>, <code>diarization_backend</code>, <code>num_speakers</code>, "
                "<code>asr_backend</code>, <code>onnx_provider</code>, and <code>audio_preprocessing</code>.<br><br>"
                "The response is returned synchronously, in the format set by <code>response_format</code> "
                "(<code>json</code>/<code>text</code>/<code>srt</code>/<code>vtt</code>/<code>verbose_json</code>/<code>diarized_json</code>). "
                "The interactive schema is available at <code>/docs</code> while the service runs.",
            ),
        ))
        layout.addStretch()
        return panel

    def _api_examples(self) -> dict[str, str]:
        base_url = self._api_base_url()
        return {
            "Python": (
                "import os\n"
                "from openai import OpenAI\n\n"
                f"client = OpenAI(base_url=\"{base_url}/v1\", api_key=os.environ[\"GIGAAM_API_KEY\"])\n"
                "with open(\"meeting.mp3\", \"rb\") as audio:\n"
                "    result = client.audio.transcriptions.create(\n"
                "        model=\"whisper-1\", file=audio, response_format=\"verbose_json\",\n"
                "    )\n"
                "print(result.text)\n"
            ),
            "cURL": (
                f"BASE_URL={base_url}\n"
                "curl \"$BASE_URL/v1/audio/transcriptions\" \\\n"
                "  -H \"Authorization: Bearer $GIGAAM_API_KEY\" \\\n"
                "  -F \"file=@meeting.mp3\" -F \"model=whisper-1\" -F \"response_format=verbose_json\"\n"
            ),
            "JavaScript": (
                "import OpenAI from \"openai\";\n"
                "import fs from \"node:fs\";\n\n"
                "const apiKey = \"gam_...\";\n"
                f"const client = new OpenAI({{ baseURL: \"{base_url}/v1\", apiKey }});\n"
                "const result = await client.audio.transcriptions.create({\n"
                "  model: \"whisper-1\",\n"
                "  file: fs.createReadStream(\"meeting.mp3\"),\n"
                "  response_format: \"verbose_json\",\n"
                "});\n"
                "console.log(result.text);\n"
            ),
        }

    def _api_base_url(self) -> str:
        raw_url = getattr(self, "api_endpoint_input", None)
        value = raw_url.text().strip() if raw_url is not None else ""
        return value.rstrip("/") or self._API_DEFAULT_URL

    def _save_api_base_url(self) -> None:
        base_url = self._api_base_url()
        self.api_endpoint_input.setText(base_url)
        self.user_settings.set_value("api_base_url", base_url)
        if hasattr(self, "settings_api_endpoint"):
            self.settings_api_endpoint.blockSignals(True)
            self.settings_api_endpoint.setText(base_url)
            self.settings_api_endpoint.blockSignals(False)
        for language, editor in getattr(self, "api_code_edits", {}).items():
            editor.setPlainText(self._api_examples()[language])
        self._refresh_api_status()

    @staticmethod
    def _api_health_available(base_url: str) -> bool:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        try:
            with urlopen(f"{base_url}/health", timeout=0.35) as response:
                return response.status == 200
        except (OSError, URLError, ValueError):
            return False

    def _refresh_api_status(self) -> None:
        """Проверить /health в фоне: запрос к недоступному адресу — это таймаут,
        и раньше он шёл в Qt-потоке при построении окна и каждой правке адреса."""
        base_url = self._api_base_url()
        self._api_available = None
        self._render_api_status()
        signals = self.signals

        def probe():
            available = self._api_health_available(base_url)
            try:
                signals.api_status_checked.emit(base_url, available)
            except RuntimeError:
                pass  # окно уже закрыто

        threading.Thread(target=probe, name="api-health", daemon=True).start()

    def _on_api_status_checked(self, base_url: str, available: bool) -> None:
        if base_url != self._api_base_url():
            return  # ответ на прежний адрес: уже идёт проверка нового
        self._api_available = available
        self._render_api_status()

    def _render_api_status(self) -> None:
        """Статус сервиса на текущем языке: None — проверка ещё идёт."""
        available = getattr(self, "_api_available", None)
        if available is None:
            self.api_status_label.setText(self._t("… Проверка", "… Checking"))
        else:
            self.api_status_label.setText(
                self._t("● Запущен", "● Running") if available else self._t("○ Остановлен", "○ Stopped")
            )
        self.api_status_label.setStyleSheet(
            "color: #22A06B;" if available else "color: #667085;"
        )
        self.api_docs_button.setEnabled(bool(available))
        self.api_docs_button.setToolTip(
            "" if available else self._t("Документация доступна после запуска API-сервиса.", "Documentation is available after the API service starts.")
        )

    def _retranslate_api_tab(self, _is_ru: bool) -> None:
        """Вкладка API: статичные подписи переводит _bilingual, здесь — статус."""
        if hasattr(self, "api_status_label"):
            self._render_api_status()

    def _copy_api_endpoint(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._api_base_url())
            self._set_status(self._t("Адрес API скопирован", "API address copied"))

    def _copy_active_api_example(self) -> None:
        current = self.api_code_tabs.currentWidget()
        if current is None:
            return
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(current.toPlainText())
            self._set_status(self._t("Пример запроса скопирован", "Request example copied"))

    def _open_api_documentation(self) -> None:
        if getattr(self, "_api_available", False):
            QDesktopServices.openUrl(QUrl(f"{self._api_base_url()}/docs"))
