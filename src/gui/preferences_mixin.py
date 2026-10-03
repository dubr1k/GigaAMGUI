"""Вкладка «Настройки»: общие, модели, аудио, LLM, API, интерфейс.

Это второе представление настроек, которые живут и в меню, и на вкладках
«Обработка»/LLM, и в диалоге LLM: страница только пересылает изменения туда
и синхронизируется из них (_sync_support_surface_settings).

Mixin: методы работают со `self` главного окна.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.asr.models import ASR_MODELS


class PreferencesMixin:
    """Build the desktop preferences surface."""

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
            # Провайдер узнаётся по каноническому имени в itemData: подпись
            # «Другое»/«Other» меняется с языком, и findText по ней терял выбор.
            for index in range(self.combo_llm_provider.count()):
                self.settings_llm_provider.addItem(
                    self.combo_llm_provider.itemText(index), self.combo_llm_provider.itemData(index),
                )
        self.settings_llm_provider.currentIndexChanged.connect(self._set_settings_llm_provider)
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

    def _can_change_processing_settings(self) -> bool:
        if not self.is_processing:
            return True
        self._set_status(self._t("Дождитесь завершения обработки.", "Wait for processing to finish."))
        self._sync_support_surface_settings()
        return False

    def _set_settings_model(self) -> None:
        model = self.settings_model_combo.currentData()
        if self._refuse_model_change_while_busy(self._t("Смена модели", "Model change")):
            self._sync_support_surface_settings()
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
        if hasattr(self, "cb_diarization"):
            self.cb_diarization.setChecked(checked)
            # Включение pyannote без HF_TOKEN спрашивает токен, и отмена
            # возвращает флажок назад: сохраняем то, что осталось на деле.
            checked = self.cb_diarization.isChecked()
        self.user_settings.set_value("enable_diarization", checked)
        self._sync_support_surface_settings()

    def _set_settings_speakers(self, count: int) -> None:
        if not self._can_change_processing_settings():
            return
        if hasattr(self, "entry_num_speakers"):
            self.entry_num_speakers.setValue(count)
        self.user_settings.set_value("num_speakers", count)

    def _set_settings_llm_provider(self, _index: int | None = None) -> None:
        provider = self.settings_llm_provider.currentData()
        if not provider:
            return
        if hasattr(self, "combo_llm_provider"):
            index = self.combo_llm_provider.findData(provider)
            if index >= 0:
                self.combo_llm_provider.setCurrentIndex(index)
                self._update_llm_provider_fields(self.combo_llm_provider.currentText())
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
            self.settings_llm_provider.blockSignals(True)
            for index in range(self.settings_llm_provider.count()):
                source = self.combo_llm_provider.findData(self.settings_llm_provider.itemData(index))
                if source >= 0:
                    self.settings_llm_provider.setItemText(index, self.combo_llm_provider.itemText(source))
            index = self.settings_llm_provider.findData(self.combo_llm_provider.currentData())
            if index >= 0:
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
