"""Строка меню и действия меню, которые меняют модель, движок и устройство.

Mixin: методы работают со `self` главного окна.
"""
from __future__ import annotations

from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QMessageBox

from .. import __version__ as APP_VERSION
from ..config import APP_TITLE
from .asr_backend_dialog import ASRBackendDialog, is_mlx_supported


class MenuActionsMixin:
    def _build_menu_bar(self):
        menubar = self.menuBar()
        menubar.clear()

        self._menu_file = menubar.addMenu("Файл")
        file_menu = self._menu_file
        self._act_files = QAction("Выбрать файлы…", self)
        act_files = self._act_files
        act_files.setShortcut(QKeySequence.StandardKey.Open)
        self._bilingual(act_files.setStatusTip, "Добавить аудио- или видеофайлы в очередь", "Add audio or video files to the queue")
        act_files.triggered.connect(self._select_files)
        file_menu.addAction(act_files)

        self._act_folder = QAction("Выбрать папку с файлами…", self)
        act_folder = self._act_folder
        self._bilingual(act_folder.setStatusTip, "Добавить все медиафайлы из папки и подпапок", "Add all media files from the folder and subfolders")
        act_folder.triggered.connect(self._select_files_folder)
        file_menu.addAction(act_folder)

        self._act_out = QAction("Папка сохранения…", self)
        act_out = self._act_out
        self._bilingual(act_out.setStatusTip, "Выбрать папку для результатов транскрибации", "Choose the folder for transcription results")
        act_out.triggered.connect(self._select_output_folder)
        file_menu.addAction(act_out)

        file_menu.addSeparator()
        self._act_open_res = QAction("Открыть папку с результатами", self)
        act_open_res = self._act_open_res
        self._bilingual(act_open_res.setStatusTip, "Открыть папку с готовыми файлами", "Open the folder with finished files")
        act_open_res.triggered.connect(self._open_results_folder)
        file_menu.addAction(act_open_res)

        file_menu.addSeparator()
        self._act_quit = QAction("Выход", self)
        act_quit = self._act_quit
        act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        self._menu_view = menubar.addMenu("Вид")
        view_menu = self._menu_view
        self._act_theme = QAction("Переключить тему", self)
        self._act_theme.setShortcut(QKeySequence("Ctrl+T"))
        self._bilingual(self._act_theme.setStatusTip, "Светлая / тёмная тема оформления", "Light / dark theme")
        self._act_theme.triggered.connect(self._toggle_theme)
        view_menu.addAction(self._act_theme)

        self._act_accent = QAction("Акцентный цвет…", self)
        self._bilingual(self._act_accent.setStatusTip, "Выбрать акцентный цвет интерфейса", "Choose the interface accent color")
        self._act_accent.triggered.connect(self._choose_accent_color)
        view_menu.addAction(self._act_accent)

        self._act_accent_reset = QAction("Сбросить акцентный цвет", self)
        self._bilingual(self._act_accent_reset.setStatusTip, "Вернуть стандартный акцентный цвет", "Restore the default accent color")
        self._act_accent_reset.triggered.connect(self._reset_accent_color)
        view_menu.addAction(self._act_accent_reset)

        self._menu_settings = menubar.addMenu("Настройки")
        settings_menu = self._menu_settings
        self._act_asr_model = QAction("Модель распознавания…", self)
        self._bilingual(self._act_asr_model.setStatusTip, "Выбрать модель GigaAM для следующей обработки", "Choose the GigaAM model for the next run")
        self._act_asr_model.triggered.connect(self._select_asr_model)
        settings_menu.addAction(self._act_asr_model)

        self._act_asr_backend = QAction("Движок распознавания…", self)
        act_asr_backend = self._act_asr_backend
        self._bilingual(act_asr_backend.setStatusTip, "Выбрать backend для распознавания речи", "Choose the speech recognition backend")
        act_asr_backend.triggered.connect(self._select_asr_backend)
        settings_menu.addAction(act_asr_backend)

        settings_menu.addSeparator()
        self._act_device = QAction("Устройство (CPU / GPU)…", self)
        act_device = self._act_device
        self._bilingual(act_device.setStatusTip, "Выбрать CPU или видеокарту NVIDIA для распознавания", "Choose CPU or an NVIDIA GPU for recognition")
        act_device.triggered.connect(self._change_device)
        settings_menu.addAction(act_device)

        settings_menu.addSeparator()
        self._act_data_dir = QAction("Папка данных и моделей…", self)
        self._act_data_dir.setStatusTip("Выбрать диск для моделей, кэшей и runtime")
        self._act_data_dir.triggered.connect(self._select_data_directory)
        settings_menu.addAction(self._act_data_dir)

        settings_menu.addSeparator()
        self._act_llm = QAction("LLM API…", self)
        act_llm = self._act_llm
        self._bilingual(act_llm.setStatusTip, "Настроить API URL, ключ, модель и папку результатов LLM", "Configure the LLM API URL, key, model and results folder")
        act_llm.triggered.connect(self._open_llm_settings_dialog)
        settings_menu.addAction(act_llm)

        self._menu_help = menubar.addMenu("Справка")
        help_menu = self._menu_help
        self._act_about = QAction("О программе", self)
        act_about = self._act_about
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

    def _show_about(self):
        diag = self.model_loader.diagnostics() if self.model_loader is not None else {}
        diag_lines = [
            f"requested_backend={diag.get('requested_backend')}",
            f"active_backend={diag.get('active_backend')}",
            f"model={diag.get('model')}",
            f"device={diag.get('device')}",
            f"repo={diag.get('repo')}",
            f"fallback_reason={diag.get('fallback_reason')}",
            f"cache_root={diag.get('cache_root')}",
        ]
        diagnostics = "<br>".join(diag_lines)
        QMessageBox.about(
            self,
            self._t("О программе", "About"),
            (
                f"<b>{APP_TITLE}</b><br>Версия {APP_VERSION}<br><br>"
                "Локальная транскрибация аудио и видео на модели <b>GigaAM v3</b> с поддержкой диаризации спикеров.<br><br>"
                "Возможности: пакетная обработка, загрузка по ссылке, таймкоды, экспорт в TXT / Markdown / SRT / VTT.<br><br>"
                f"Диагностика ASR:<br>{diagnostics}<br><br>"
                "Поддерживаемые форматы ввода: mp3, wav, m4a, aac, flac, ogg, mp4, avi, mov, mkv, webm, wma, 3gp."
            ) if self._lang == "ru" else (
                f"<b>{APP_TITLE}</b><br>Version {APP_VERSION}<br><br>"
                "Local audio and video transcription powered by <b>GigaAM v3</b> with speaker diarization support.<br><br>"
                "Features: batch processing, URL download, timecodes, export to TXT / Markdown / SRT / VTT.<br><br>"
                f"ASR diagnostics:<br>{diagnostics}<br><br>"
                "Supported input formats: mp3, wav, m4a, aac, flac, ogg, mp4, avi, mov, mkv, webm, wma, 3gp."
            )
        )

    def _select_asr_model(self):
        if self._refuse_model_change_while_busy(self._t("Смена модели", "Model change")):
            return
        from PyQt6.QtWidgets import QInputDialog

        from ..core.asr.models import ASR_MODELS

        ids = list(ASR_MODELS)
        labels = [f"{ASR_MODELS[key]} [{key}]" for key in ids]
        current = ids.index(self.model_loader.requested_model) if self.model_loader.requested_model in ids else 0
        selected, accepted = QInputDialog.getItem(self, self._t("Модель распознавания", "Recognition model"), self._t("Модель:", "Model:"), labels, current, False)
        if accepted:
            model = ids[labels.index(selected)]
            self.model_loader.configure_model(model)
            self.user_settings.set_value("asr_model", model)
            self._sync_support_surface_settings()
            self.log(f"ASR model selected: {model}")

    def _select_asr_backend(self):
        if self._refuse_model_change_while_busy(self._t("Смена backend", "Backend change")):
            return

        selected = ASRBackendDialog.pick_configuration(
            self,
            current_backend=self.model_loader.requested_backend,
            current_provider=self.model_loader.requested_provider,
            mlx_supported=is_mlx_supported(),
        )

        if not selected:
            return

        backend, provider = selected
        if (
            backend == self.model_loader.requested_backend
            and provider == self.model_loader.requested_provider
        ):
            return
        self.model_loader.configure_backend(backend)
        self.model_loader.configure_onnx_runtime(provider=provider)
        self.user_settings.set_value("asr_backend", backend)
        self.user_settings.set_value("onnx_provider", provider)
        self._sync_support_surface_settings()
        self.log(
            f"Выбран ASR backend: {backend}, ONNX provider: {provider}"
            if self._lang == "ru"
            else f"ASR backend selected: {backend}, ONNX provider: {provider}"
        )

    def _change_device(self):
        """Смена вычислительного устройства (CPU / GPU / GPU 50xx) из меню."""
        from .device_dialog import change_device_interactive

        # Смена устройства выгружает модель и подменяет torch-runtime —
        # из-под идущей Live-записи тоже, не только из-под пакетной обработки.
        if self._refuse_model_change_while_busy(self._t("Устройство", "Device")):
            return

        chosen = change_device_interactive(self)
        if chosen:
            label = chosen
            try:
                from ..utils import runtime_manager as rm
                label = rm.VARIANTS.get(chosen, {}).get("label", chosen)
            except Exception:
                pass
            self.log(
                f"Активировано устройство: {label}"
                if self._lang == "ru" else
                f"Active device: {label}"
            )

    def _retranslate_menu(self, is_ru: bool) -> None:
        """Строка меню."""
        if not hasattr(self, "_menu_file"):
            return
        self._menu_file.setTitle("Файл" if is_ru else "File")
        self._menu_view.setTitle("Вид" if is_ru else "View")
        self._menu_settings.setTitle("Настройки" if is_ru else "Settings")
        if hasattr(self, "_act_data_dir"):
            self._act_data_dir.setText("Папка данных и моделей…" if is_ru else "Data and model directory…")
            self._act_data_dir.setStatusTip("Выбрать диск для моделей, кэшей и runtime" if is_ru else "Choose a drive for models, caches, and runtimes")
        self._menu_help.setTitle("Справка" if is_ru else "Help")
        self._act_files.setText("Выбрать файлы…" if is_ru else "Choose files…")
        self._act_folder.setText("Выбрать папку с файлами…" if is_ru else "Choose folder with files…")
        self._act_out.setText("Папка сохранения…" if is_ru else "Output folder…")
        self._act_open_res.setText("Открыть папку с результатами" if is_ru else "Open results folder")
        self._act_quit.setText("Выход" if is_ru else "Exit")
        self._act_theme.setText("Переключить тему" if is_ru else "Toggle theme")
        self._act_accent.setText("Акцентный цвет…" if is_ru else "Accent color…")
        self._act_accent_reset.setText("Сбросить акцентный цвет" if is_ru else "Reset accent color")
        if hasattr(self, "_act_asr_model"):
            self._act_asr_model.setText("Модель распознавания…" if is_ru else "Recognition model...")
        self._act_asr_backend.setText("Движок распознавания…" if is_ru else "Recognition engine...")
        self._act_device.setText("Устройство (CPU / GPU)…" if is_ru else "Device (CPU / GPU)…")
        self._act_llm.setText("LLM API…")
        self._act_about.setText("О программе" if is_ru else "About")
