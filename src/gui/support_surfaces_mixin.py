"""API documentation and settings surfaces for the desktop application."""
from __future__ import annotations

import os
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.asr.models import ASR_MODELS


class SupportSurfacesMixin:
    """Build desktop-only API documentation and preferences surfaces."""

    _API_DEFAULT_URL = "http://127.0.0.1:8000"

    def _create_api_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("api_page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(self._px(12), self._px(12), self._px(12), self._px(12))
        layout.setSpacing(self._px(10))

        heading = QLabel(self._t("API", "API"))
        heading.setObjectName("support_heading")
        heading.setFont(self._font(18))
        layout.addWidget(heading)

        status_card = QFrame()
        status_card.setObjectName("api_status_bar")
        status_layout = QHBoxLayout(status_card)
        status_layout.setContentsMargins(self._px(12), self._px(9), self._px(12), self._px(9))
        status_layout.setSpacing(self._px(8))
        status_title = QLabel(self._t("Статус API", "API status"))
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

        refresh = QPushButton(self._t("Проверить", "Refresh"))
        refresh.setObjectName("api_action_button")
        refresh.clicked.connect(self._refresh_api_status)
        status_layout.addWidget(refresh)
        copy_endpoint = QPushButton(self._t("Скопировать", "Copy"))
        copy_endpoint.setObjectName("api_action_button")
        copy_endpoint.clicked.connect(self._copy_api_endpoint)
        status_layout.addWidget(copy_endpoint)
        self.api_docs_button = QPushButton(self._t("Открыть документацию", "Open documentation"))
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
        title = QLabel(self._t("Примеры запросов", "Request examples"))
        title.setObjectName("api_panel_title")
        title.setFont(self._font(11))
        title_row.addWidget(title)
        title_row.addStretch()
        copy_button = QPushButton(self._t("Копировать", "Copy"))
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
            editor.setStyleSheet(
                "background: #F7FAFD; color: #243B53; border: 1px solid #DBE5EF; "
                f"border-radius: {self._px(10)}px;"
            )
            self.api_code_edits[language] = editor
            self.api_code_tabs.addTab(editor, language)
        layout.addWidget(self.api_code_tabs, 1)
        return panel

    def _api_documentation_section(self, title: str, body: str, expanded: bool = False) -> QWidget:
        section = QFrame()
        section.setObjectName("api_doc_section")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(self._px(10), self._px(8), self._px(10), self._px(8))
        layout.setSpacing(self._px(5))
        toggle = QToolButton()
        toggle.setObjectName("api_doc_toggle")
        toggle.setText(title)
        toggle.setCheckable(True)
        toggle.setChecked(expanded)
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        layout.addWidget(toggle)
        content = QLabel(body)
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

        title = QLabel(self._t("Документация", "Documentation"))
        title.setObjectName("api_panel_title")
        title.setFont(self._font(11))
        layout.addWidget(title)
        layout.addWidget(self._api_documentation_section(
            self._t("Быстрый старт", "Quick start"),
            self._t(
                "Запустите отдельный API-сервис командой <code>python api.py</code>. "
                "Передавайте ключ в заголовке <code>X-API-Key</code>.",
                "Start the separate API service with <code>python api.py</code>. "
                "Send the key in the <code>X-API-Key</code> header.",
            ),
            expanded=True,
        ))
        layout.addWidget(self._api_documentation_section(
            self._t("Эндпоинты", "Endpoints"),
            self._t(
                "<code>POST /api/v1/transcribe</code> — один файл.<br>"
                "<code>POST /api/v1/transcribe/batch</code> — до 10 файлов.<br>"
                "<code>GET /api/v1/tasks/{task_id}</code> — статус.<br>"
                "<code>GET /api/v1/tasks/{task_id}/result</code> — готовый текст.<br>"
                "<code>GET /api/v1/tasks/{task_id}/download?format=txt|timecodes</code> — скачать файл.",
                "<code>POST /api/v1/transcribe</code> — one file.<br>"
                "<code>POST /api/v1/transcribe/batch</code> — up to 10 files.<br>"
                "<code>GET /api/v1/tasks/{task_id}</code> — status.<br>"
                "<code>GET /api/v1/tasks/{task_id}/result</code> — completed text.<br>"
                "<code>GET /api/v1/tasks/{task_id}/download?format=txt|timecodes</code> — download a file.",
            ),
        ))
        layout.addWidget(self._api_documentation_section(
            self._t("Параметры и ответы", "Parameters and responses"),
            self._t(
                "Загрузка принимает <code>file</code>; необязательные параметры: "
                "<code>asr_backend</code>, <code>asr_model</code>, <code>onnx_provider</code>, "
                "<code>enable_diarization</code>, <code>diarization_backend</code>, <code>num_speakers</code>.<br><br>"
                "Успешная загрузка возвращает <code>202</code> и <code>task_id</code>. "
                "Интерактивная схема доступна в <code>/docs</code>, когда сервис запущен.",
                "Uploads require <code>file</code>; optional parameters are "
                "<code>asr_backend</code>, <code>asr_model</code>, <code>onnx_provider</code>, "
                "<code>enable_diarization</code>, <code>diarization_backend</code>, and <code>num_speakers</code>.<br><br>"
                "A successful upload returns <code>202</code> and <code>task_id</code>. "
                "The interactive schema is available at <code>/docs</code> while the service runs.",
            ),
        ))
        layout.addStretch()
        return panel

    def _api_examples(self) -> dict[str, str]:
        base_url = self._api_base_url()
        return {
            "Python": (
                "import os\nimport requests\n\n"
                f"base_url = \"{base_url}\"\n"
                "headers = {\"X-API-Key\": os.environ[\"GIGAAM_API_KEY\"]}\n"
                "with open(\"meeting.mp3\", \"rb\") as audio:\n"
                "    response = requests.post(\n"
                "        f\"{base_url}/api/v1/transcribe\",\n"
                "        headers=headers, files={\"file\": audio}, timeout=30,\n"
                "    )\n"
                "response.raise_for_status()\n"
                "task_id = response.json()[\"task_id\"]\n"
                "status = requests.get(\n"
                "    f\"{base_url}/api/v1/tasks/{task_id}\", headers=headers, timeout=30\n"
                ").json()\n"
            ),
            "cURL": (
                f"BASE_URL={base_url}\n"
                "curl -X POST \"$BASE_URL/api/v1/transcribe\" \\\n"
                "  -H \"X-API-Key: $GIGAAM_API_KEY\" \\\n"
                "  -F \"file=@meeting.mp3\"\n\n"
                "curl \"$BASE_URL/api/v1/tasks/TASK_ID\" \\\n"
                "  -H \"X-API-Key: $GIGAAM_API_KEY\"\n"
            ),
            "JavaScript": (
                f"const baseUrl = \"{base_url}\";\n"
                "const form = new FormData();\n"
                "form.append(\"file\", fileInput.files[0]);\n\n"
                "const response = await fetch(`${baseUrl}/api/v1/transcribe`, {\n"
                "  method: \"POST\",\n"
                "  headers: { \"X-API-Key\": apiKey },\n"
                "  body: form,\n"
                "});\n"
                "if (!response.ok) throw new Error(await response.text());\n"
                "const { task_id } = await response.json();\n"
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

    def _api_health_available(self) -> bool:
        parsed = urlsplit(self._api_base_url())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        try:
            with urlopen(f"{self._api_base_url()}/health", timeout=0.35) as response:
                return response.status == 200
        except (OSError, URLError, ValueError):
            return False

    def _refresh_api_status(self) -> None:
        available = self._api_health_available()
        self._api_available = available
        self.api_status_label.setText(
            self._t("● Запущен", "● Running") if available else self._t("○ Остановлен", "○ Stopped")
        )
        self.api_status_label.setStyleSheet(
            "color: #22A06B;" if available else "color: #667085;"
        )
        self.api_docs_button.setEnabled(available)
        self.api_docs_button.setToolTip(
            "" if available else self._t("Документация доступна после запуска API-сервиса.", "Documentation is available after the API service starts.")
        )

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

    def _create_settings_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("settings_page")
        root = QVBoxLayout(page)
        root.setContentsMargins(self._px(12), self._px(12), self._px(12), self._px(12))
        root.setSpacing(self._px(10))

        title = QLabel(self._t("Настройки", "Settings"))
        title.setObjectName("support_heading")
        title.setFont(self._font(18))
        root.addWidget(title)

        category_bar = QFrame()
        category_bar.setObjectName("settings_category_bar")
        category_layout = QVBoxLayout(category_bar)
        category_layout.setContentsMargins(self._px(4), self._px(4), self._px(4), self._px(4))
        self.settings_categories = QListWidget()
        self.settings_categories.setObjectName("settings_category_tabs")
        self.settings_categories.setViewMode(QListView.ViewMode.IconMode)
        self.settings_categories.setFlow(QListView.Flow.LeftToRight)
        self.settings_categories.setWrapping(False)
        self.settings_categories.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.settings_categories.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.settings_categories.setFixedHeight(self._px(38))
        category_layout.addWidget(self.settings_categories)
        root.addWidget(category_bar)

        self.settings_stack = QStackedWidget()
        self.settings_stack.setObjectName("settings_preferences_stack")
        for name, builder in (
            (self._t("Общие", "General"), self._create_general_settings),
            (self._t("Модели", "Models"), self._create_models_settings),
            (self._t("Аудио", "Audio"), self._create_audio_settings),
            ("LLM", self._create_llm_settings),
            ("API", self._create_api_settings),
            (self._t("Интерфейс", "Interface"), self._create_interface_settings),
            (self._t("Экспериментальные", "Experimental"), self._create_experimental_settings),
        ):
            item = QListWidgetItem(name)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.settings_categories.addItem(item)
            self.settings_stack.addWidget(builder())
        self.settings_categories.currentRowChanged.connect(self._set_settings_category)
        root.addWidget(self.settings_stack, 1)

        initial_category = int(self.user_settings.get_value("settings_category", 0) or 0)
        self.settings_categories.setCurrentRow(max(0, min(initial_category, self.settings_stack.count() - 1)))
        return page

    def _settings_page(self, title: str, subtitle: str) -> tuple[QScrollArea, QVBoxLayout]:
        content = QFrame()
        content.setObjectName("settings_form_panel")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(self._px(16), self._px(14), self._px(16), self._px(14))
        layout.setSpacing(self._px(10))
        heading = QLabel(title)
        heading.setObjectName("settings_section_heading")
        heading.setFont(self._font(13))
        layout.addWidget(heading)
        description = QLabel(subtitle)
        description.setObjectName("settings_section_description")
        description.setWordWrap(True)
        layout.addWidget(description)
        scroll = QScrollArea()
        scroll.setObjectName("settings_form_scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        return scroll, layout

    def _form_row(self, form: QFormLayout, label: str, control: QWidget) -> None:
        label_widget = QLabel(label)
        label_widget.setObjectName("settings_field_label")
        if not control.objectName():
            control.setObjectName("settings_field_control")
        form.addRow(label_widget, control)

    def _create_general_settings(self) -> QWidget:
        page, layout = self._settings_page(
            self._t("Общие", "General"),
            self._t("Язык, тема и папки, которые использует приложение.", "Language, theme, and application folders."),
        )
        form = QFormLayout()
        form.setObjectName("settings_preferences_form")
        form.setSpacing(self._px(10))
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.settings_language = QComboBox()
        self.settings_language.addItem(self._t("Русский", "Russian"), "ru")
        self.settings_language.addItem(self._t("English", "English"), "en")
        self.settings_language.currentIndexChanged.connect(self._set_settings_language)
        self._form_row(form, self._t("Язык интерфейса", "Interface language"), self.settings_language)
        self.settings_theme = QComboBox()
        self.settings_theme.addItem(self._t("Светлая", "Light"), "light")
        self.settings_theme.addItem(self._t("Тёмная", "Dark"), "dark")
        self.settings_theme.currentIndexChanged.connect(self._set_settings_theme)
        self._form_row(form, self._t("Тема", "Theme"), self.settings_theme)

        paths_title = QLabel(self._t("Пути", "Paths"))
        paths_title.setObjectName("settings_section_label")
        paths_title.setFont(self._font(11))
        form.addRow(paths_title)
        data_control = QWidget()
        data_control.setObjectName("settings_path_control")
        data_row = QHBoxLayout(data_control)
        data_row.setContentsMargins(0, 0, 0, 0)
        self.settings_data_dir_value = QLabel(os.environ.get("GIGAAM_DATA_DIR", self._t("По умолчанию", "Default")))
        self.settings_data_dir_value.setObjectName("settings_path_value")
        self.settings_data_dir_value.setWordWrap(True)
        data_row.addWidget(self.settings_data_dir_value, 1)
        choose_data = QPushButton(self._t("Папка данных и моделей…", "Data and models folder…"))
        choose_data.setObjectName("settings_path_button")
        choose_data.clicked.connect(self._select_data_directory)
        data_row.addWidget(choose_data)
        self._form_row(form, self._t("Данные и модели", "Data and models"), data_control)

        output_control = QWidget()
        output_control.setObjectName("settings_path_control")
        output_row = QHBoxLayout(output_control)
        output_row.setContentsMargins(0, 0, 0, 0)
        self.settings_output_dir_value = QLabel()
        self.settings_output_dir_value.setObjectName("settings_path_value")
        self.settings_output_dir_value.setWordWrap(True)
        output_row.addWidget(self.settings_output_dir_value, 1)
        choose_output = QPushButton(self._t("Папка результатов…", "Results folder…"))
        choose_output.setObjectName("settings_path_button")
        choose_output.clicked.connect(self._select_settings_output_folder)
        output_row.addWidget(choose_output)
        self._form_row(form, self._t("Результаты", "Results"), output_control)
        layout.addLayout(form)
        layout.addStretch()
        return page

    def _create_models_settings(self) -> QWidget:
        page, layout = self._settings_page(
            self._t("Модели", "Models"),
            self._t("Настройки используются следующей обработкой файлов.", "These settings are used by the next file processing run."),
        )
        form = QFormLayout()
        form.setObjectName("settings_preferences_form")
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setSpacing(self._px(12))
        self.settings_model_combo = QComboBox()
        for model_id, model_name in ASR_MODELS.items():
            self.settings_model_combo.addItem(f"{model_name} [{model_id}]", model_id)
        self.settings_model_combo.currentIndexChanged.connect(self._set_settings_model)
        self._form_row(form, self._t("Модель", "Model"), self.settings_model_combo)
        self.settings_backend_button = QPushButton()
        self.settings_backend_button.clicked.connect(self._choose_settings_backend)
        self._form_row(form, self._t("ASR backend", "ASR backend"), self.settings_backend_button)
        self.settings_device_button = QPushButton(self._t("Изменить устройство…", "Change device…"))
        self.settings_device_button.clicked.connect(self._choose_settings_device)
        self._form_row(form, self._t("Устройство", "Device"), self.settings_device_button)
        layout.addLayout(form)
        layout.addStretch()
        return page

    def _create_audio_settings(self) -> QWidget:
        page, layout = self._settings_page(
            self._t("Аудио", "Audio"),
            self._t("Подготовка записи и распознавание спикеров.", "Recording preparation and speaker recognition."),
        )
        form = QFormLayout()
        form.setObjectName("settings_preferences_form")
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setSpacing(self._px(12))
        self.settings_audio_preprocessing = QComboBox()
        if hasattr(self, "combo_audio_preprocessing"):
            for index in range(self.combo_audio_preprocessing.count()):
                self.settings_audio_preprocessing.addItem(
                    self.combo_audio_preprocessing.itemText(index),
                    self.combo_audio_preprocessing.itemData(index),
                )
        self.settings_audio_preprocessing.currentIndexChanged.connect(self._set_settings_audio_preprocessing)
        self._form_row(form, self._t("Подготовка аудио", "Audio preparation"), self.settings_audio_preprocessing)
        self.settings_diarization = QPushButton()
        self.settings_diarization.setCheckable(True)
        self.settings_diarization.clicked.connect(self._set_settings_diarization)
        self._form_row(form, self._t("Диаризация", "Diarization"), self.settings_diarization)
        self.settings_speakers = QSpinBox()
        self.settings_speakers.setMinimum(0)
        self.settings_speakers.setMaximum(32)
        self.settings_speakers.setSpecialValueText(self._t("Авто", "Auto"))
        self.settings_speakers.valueChanged.connect(self._set_settings_speakers)
        self._form_row(form, self._t("Количество спикеров", "Speaker count"), self.settings_speakers)
        layout.addLayout(form)
        layout.addStretch()
        return page

    def _create_llm_settings(self) -> QWidget:
        page, layout = self._settings_page(
            "LLM",
            self._t("Провайдер и параметры постобработки транскрипций.", "Provider and transcription post-processing parameters."),
        )
        form = QFormLayout()
        form.setObjectName("settings_preferences_form")
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setSpacing(self._px(12))
        self.settings_llm_provider = QComboBox()
        if hasattr(self, "combo_llm_provider"):
            for index in range(self.combo_llm_provider.count()):
                self.settings_llm_provider.addItem(self.combo_llm_provider.itemText(index))
        self.settings_llm_provider.currentTextChanged.connect(self._set_settings_llm_provider)
        self._form_row(form, self._t("Провайдер", "Provider"), self.settings_llm_provider)
        self.settings_llm_api_url = QLineEdit()
        self.settings_llm_api_url.editingFinished.connect(self._save_settings_llm_fields)
        self._form_row(form, "API URL", self.settings_llm_api_url)
        self.settings_llm_model = QLineEdit()
        self.settings_llm_model.editingFinished.connect(self._save_settings_llm_fields)
        self._form_row(form, self._t("Модель", "Model"), self.settings_llm_model)
        self.settings_llm_temperature = QLineEdit()
        self.settings_llm_temperature.editingFinished.connect(self._save_settings_llm_fields)
        self._form_row(form, self._t("Температура", "Temperature"), self.settings_llm_temperature)
        layout.addLayout(form)
        advanced = QPushButton(self._t("Расширенные настройки LLM…", "Advanced LLM settings…"))
        advanced.setObjectName("settings_secondary_action")
        advanced.clicked.connect(self._open_llm_settings_dialog)
        layout.addWidget(advanced, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
        return page

    def _create_api_settings(self) -> QWidget:
        page, layout = self._settings_page(
            "API",
            self._t("Адрес используется для документации и проверки доступности отдельного API-сервиса.", "The address is used by documentation and to check the separate API service."),
        )
        form = QFormLayout()
        form.setObjectName("settings_preferences_form")
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.settings_api_endpoint = QLineEdit(self._api_base_url())
        self.settings_api_endpoint.editingFinished.connect(self._save_settings_api_endpoint)
        self._form_row(form, self._t("Адрес API", "API address"), self.settings_api_endpoint)
        layout.addLayout(form)
        layout.addStretch()
        return page

    def _create_interface_settings(self) -> QWidget:
        page, layout = self._settings_page(
            self._t("Интерфейс", "Interface"),
            self._t("Настройте реальный акцент приложения; основная тема выбирается в «Общих».", "Set the application accent; the base theme is selected under General."),
        )
        buttons = QHBoxLayout()
        choose_accent = QPushButton(self._t("Выбрать акцент…", "Choose accent…"))
        choose_accent.setObjectName("settings_secondary_action")
        choose_accent.clicked.connect(self._choose_accent_color)
        buttons.addWidget(choose_accent)
        reset_accent = QPushButton(self._t("Сбросить акцент", "Reset accent"))
        reset_accent.setObjectName("settings_secondary_action")
        reset_accent.clicked.connect(self._reset_accent_color)
        buttons.addWidget(reset_accent)
        buttons.addStretch()
        layout.addLayout(buttons)
        layout.addStretch()
        return page

    def _create_experimental_settings(self) -> QWidget:
        page, layout = self._settings_page(
            self._t("Экспериментальные", "Experimental"),
            self._t("Экспериментальные параметры для desktop-приложения пока не доступны.", "No experimental settings are currently available for the desktop application."),
        )
        layout.addStretch()
        return page

    def _set_settings_category(self, index: int) -> None:
        if index < 0:
            return
        self.settings_stack.setCurrentIndex(index)
        self.user_settings.set_value("settings_category", index)

    def _set_settings_language(self) -> None:
        language = self.settings_language.currentData()
        if language and language != self._lang:
            self._lang = language
            self.user_settings.set_value("language", language)
            self._apply_language()

    def _set_settings_theme(self) -> None:
        theme = self.settings_theme.currentData()
        if theme and theme != self._theme:
            self._theme = theme
            self.user_settings.set_value("theme", theme)
            self._apply_theme()
            self._btn_theme.setText(self._colors()["theme_btn"])

    def _can_change_processing_settings(self) -> bool:
        if not self.is_processing:
            return True
        self._set_status(self._t("Дождитесь завершения обработки.", "Wait for processing to finish."))
        self._sync_support_surface_settings()
        return False

    def _set_settings_model(self) -> None:
        model = self.settings_model_combo.currentData()
        if not self._can_change_processing_settings():
            return
        if not model or model == self.model_loader.requested_model:
            return
        try:
            self.model_loader.configure_model(model)
        except ValueError:
            return
        self.user_settings.set_value("asr_model", model)

    def _choose_settings_backend(self) -> None:
        self._select_asr_backend()
        self._sync_support_surface_settings()

    def _choose_settings_device(self) -> None:
        self._change_device()
        self._sync_support_surface_settings()

    def _set_settings_audio_preprocessing(self) -> None:
        mode = self.settings_audio_preprocessing.currentData()
        if not self._can_change_processing_settings():
            return
        if mode is None:
            return
        if hasattr(self, "combo_audio_preprocessing"):
            index = self.combo_audio_preprocessing.findData(mode)
            if index >= 0:
                self.combo_audio_preprocessing.setCurrentIndex(index)
        self.user_settings.set_value("audio_preprocessing_mode", mode)

    def _set_settings_diarization(self, checked: bool) -> None:
        if not self._can_change_processing_settings():
            return
        self.settings_diarization.setText(
            self._t("Включена", "Enabled") if checked else self._t("Выключена", "Disabled")
        )
        if hasattr(self, "cb_diarization"):
            self.cb_diarization.setChecked(checked)
        self.user_settings.set_value("enable_diarization", checked)

    def _set_settings_speakers(self, count: int) -> None:
        if not self._can_change_processing_settings():
            return
        if hasattr(self, "entry_num_speakers"):
            self.entry_num_speakers.setValue(count)
        self.user_settings.set_value("num_speakers", count)

    def _set_settings_llm_provider(self, provider: str) -> None:
        if not provider:
            return
        if hasattr(self, "combo_llm_provider"):
            index = self.combo_llm_provider.findText(provider)
            if index >= 0:
                self.combo_llm_provider.setCurrentIndex(index)
                self._update_llm_provider_fields(provider)
        self.user_settings.set_value("llm_provider", self._normalize_llm_provider(provider))

    def _save_settings_llm_fields(self) -> None:
        fields = (
            ("llm_api_url", self.settings_llm_api_url, "entry_llm_api_url"),
            ("llm_model", self.settings_llm_model, "entry_llm_model"),
            ("llm_temperature", self.settings_llm_temperature, "entry_llm_temperature"),
        )
        for key, source, target_name in fields:
            value = source.text().strip()
            target = getattr(self, target_name, None)
            if target is not None:
                target.setText(value)
            self.user_settings.set_value(key, value)

    def _save_settings_api_endpoint(self) -> None:
        self.api_endpoint_input.setText(self.settings_api_endpoint.text().strip())
        self._save_api_base_url()

    def _select_settings_output_folder(self) -> None:
        self._select_output_folder()
        self._sync_support_surface_settings()

    def _sync_support_surface_settings(self) -> None:
        if not hasattr(self, "settings_language"):
            return
        def select_data(combo: QComboBox, value) -> None:
            index = combo.findData(value)
            if index >= 0:
                combo.blockSignals(True)
                combo.setCurrentIndex(index)
                combo.blockSignals(False)

        select_data(self.settings_language, self._lang)
        select_data(self.settings_theme, self._theme)
        select_data(self.settings_model_combo, self.model_loader.requested_model)
        self.settings_backend_button.setText(self.model_loader.requested_backend or "auto")
        if hasattr(self, "combo_audio_preprocessing"):
            select_data(self.settings_audio_preprocessing, self.combo_audio_preprocessing.currentData())
        if hasattr(self, "cb_diarization"):
            self.settings_diarization.blockSignals(True)
            self.settings_diarization.setChecked(self.cb_diarization.isChecked())
            self.settings_diarization.blockSignals(False)
            self.settings_diarization.setText(
                self._t("Включена", "Enabled") if self.cb_diarization.isChecked() else self._t("Выключена", "Disabled")
            )
        if hasattr(self, "entry_num_speakers"):
            self.settings_speakers.blockSignals(True)
            self.settings_speakers.setValue(self.entry_num_speakers.value())
            self.settings_speakers.blockSignals(False)
        if hasattr(self, "combo_llm_provider"):
            index = self.settings_llm_provider.findText(self.combo_llm_provider.currentText())
            if index >= 0:
                self.settings_llm_provider.blockSignals(True)
                self.settings_llm_provider.setCurrentIndex(index)
                self.settings_llm_provider.blockSignals(False)
        for source_name, target_name in (
            ("entry_llm_api_url", "settings_llm_api_url"),
            ("entry_llm_model", "settings_llm_model"),
            ("entry_llm_temperature", "settings_llm_temperature"),
        ):
            source = getattr(self, source_name, None)
            target = getattr(self, target_name, None)
            if source is not None and target is not None:
                target.setText(source.text())
        if hasattr(self, "settings_data_dir_value"):
            self.settings_data_dir_value.setText(os.environ.get("GIGAAM_DATA_DIR", self._t("По умолчанию", "Default")))
        if hasattr(self, "settings_output_dir_value"):
            self.settings_output_dir_value.setText(
                self.output_dir or self._t("Рядом с исходным файлом", "Next to the source file")
            )

    def _restore_support_surface_settings(self) -> None:
        self._sync_support_surface_settings()
