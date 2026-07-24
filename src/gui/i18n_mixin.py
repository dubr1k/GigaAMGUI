"""Локализация интерфейса (RU/EN) и перевод runtime-сообщений для GigaTranscriberQtApp.

Mixin: методы работают со `self` главного окна. Поведение сохранено 1:1.
"""
from __future__ import annotations

from PyQt6.QtCore import QLibraryInfo, QTranslator
from PyQt6.QtWidgets import QApplication, QDialogButtonBox

from ..config import APP_TITLE


def _install_qt_translator(app: QApplication | None, language: str) -> None:
    if app is None:
        return
    current = getattr(app, "_gigaam_qt_translator", None)
    if current is not None:
        app.removeTranslator(current)
        app._gigaam_qt_translator = None
    if language != "ru":
        return
    translator = QTranslator(app)
    translations_path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if translator.load("qtbase_ru", translations_path):
        app.installTranslator(translator)
        app._gigaam_qt_translator = translator


class I18nMixin:
    def _toggle_language(self):
        self._lang = "en" if self._lang == "ru" else "ru"
        self.user_settings.settings["language"] = self._lang
        self.user_settings._save_settings()
        self._apply_language()

    def _t(self, ru: str, en: str) -> str:
        return ru if self._lang == "ru" else en

    def _normalize_llm_provider(self, provider: str) -> str:
        return "Other" if provider in {"Другое", "Other"} else provider

    def _apply_language(self):
        is_ru = self._lang == "ru"
        _install_qt_translator(QApplication.instance(), self._lang)
        self._btn_lang.setText("EN" if is_ru else "RU")
        self.setWindowTitle(APP_TITLE if is_ru else "GigaAM v3 Transcriber")
        if hasattr(self, "_title_label"):
            self._title_label.setText("GigaAM v3: Транскрибация" if is_ru else "GigaAM v3: Transcription")
        if hasattr(self, "tabs"):
            self.tabs.setTabText(0, "Обработка" if is_ru else "Process")
            self.tabs.setTabText(1, "LLM")
            self.tabs.setTabText(2, "Журнал обработки" if is_ru else "Processing log")
        if hasattr(self, "btn_start"):
            self.btn_start.setText("ЗАПУСТИТЬ ОБРАБОТКУ" if is_ru else "START PROCESSING")
        if hasattr(self, "btn_clear"):
            self.btn_clear.setText("ОЧИСТИТЬ ВСЕ" if is_ru else "CLEAR ALL")
        if hasattr(self, "btn_llm_process"):
            self.btn_llm_process.setText("ОБРАБОТАТЬ" if is_ru else "PROCESS")
        if hasattr(self, "btn_llm_clear"):
            self.btn_llm_clear.setText("ОЧИСТИТЬ ВСЕ" if is_ru else "CLEAR ALL")
        if hasattr(self, "status_bar"):
            self.status_bar.showMessage("Готов к работе" if is_ru else "Ready to work")
        if hasattr(self, "lbl_status") and self.lbl_status.text() in {"Готов к работе", "Ready to work"}:
            self.lbl_status.setText("Готов к работе" if is_ru else "Ready to work")
        if hasattr(self, "grp_files"):
            self.grp_files.setTitle("1. Выбор файлов" if is_ru else "1. File selection")
            self.grp_output.setTitle("2. Папка сохранения результатов" if is_ru else "2. Output folder")
            self.grp_audio_preprocessing.setTitle("3. Подготовка аудио" if is_ru else "3. Audio preprocessing")
            self.grp_diarization.setTitle("4. Диаризация спикеров" if is_ru else "4. Speaker diarization")
            self.grp_formats.setTitle("5. Форматы вывода" if is_ru else "5. Output formats")
            self.lbl_overall.setText("Общий прогресс" if is_ru else "Overall progress")
            self.btn_select_files.setText("Выбрать файлы" if is_ru else "Choose files")
            self.btn_select_files.setToolTip("Выбрать аудио/видео файлы для обработки  (Ctrl+O)" if is_ru else "Choose audio/video files for processing  (Ctrl+O)")
            self.btn_select_folder.setText("Выбрать папку" if is_ru else "Choose folder")
            self.btn_select_folder.setToolTip("Добавить все медиафайлы из папки и подпапок" if is_ru else "Add all media files from the folder and subfolders")
            self.btn_upload.setText("Загрузить" if is_ru else "Download")
            self.btn_upload.setToolTip("Скачать медиа по ссылке и добавить в очередь" if is_ru else "Download media by URL and add it to the queue")
            self.btn_output_select.setText("Выбрать папку" if is_ru else "Choose folder")
            self.btn_open_result.setText("Открыть папку с результатами" if is_ru else "Open results folder")
            if hasattr(self, "lbl_output_folder") and (self.lbl_output_folder.text().startswith("Папка не выбрана") or self.lbl_output_folder.text().startswith("Folder not selected")):
                self.lbl_output_folder.setText("Папка не выбрана (по умолчанию - рядом с файлом)" if is_ru else "Folder not selected (default: next to the file)")
            self.btn_cancel.setText("Отменить" if is_ru else "Cancel")
            self.cb_diarization.setText("Включить диаризацию спикеров" if is_ru else "Enable speaker diarization")
            self.cb_diarization.setToolTip("Определять, кто из спикеров говорит (нужен HF_TOKEN)" if is_ru else "Detect which speaker is talking (HF_TOKEN required)")
            self.btn_hf_token.setText("Указать / изменить HF-токен" if is_ru else "Set / change HF token")
            self.btn_hf_token.setToolTip("Открыть настройку токена HuggingFace для диаризации" if is_ru else "Open the HuggingFace token setting for diarization")
            self.lbl_audio_preprocessing_mode.setText("Режим:" if is_ru else "Mode:")
            preprocessing_labels = (
                ("Авто (рекомендуется)", "Auto (recommended)"),
                ("Выключено", "Off"),
                ("Лёгкая очистка", "Light cleanup"),
                ("Шумоподавление", "Noise suppression"),
            )
            for index, labels in enumerate(preprocessing_labels):
                self.combo_audio_preprocessing.setItemText(index, labels[0] if is_ru else labels[1])
            self.combo_audio_preprocessing.setToolTip(
                "Авто анализирует качество записи и применяет минимально необходимую обработку"
                if is_ru else
                "Auto analyzes recording quality and applies the minimum necessary processing"
            )
            self.lbl_diarization_backend.setText("Движок:" if is_ru else "Backend:")
            self.lbl_num_speakers.setText("Кол-во спикеров:" if is_ru else "Speakers count:")
            self._update_diarization_backend_controls()
            self.entry_num_speakers.setSpecialValueText("Авто" if is_ru else "Auto")
            self.entry_num_speakers.setToolTip("0 = автоопределение количества спикеров" if is_ru else "0 = auto-detect speaker count")
            self.input_path.setPlaceholderText("Ссылка на медиа (YouTube и др.)" if is_ru else "Media URL (YouTube, etc.)")
            self.input_path.setToolTip("Вставьте ссылку и нажмите «Загрузить»" if is_ru else "Paste a link and press 'Download'")
            if not self.files_to_process:
                self.lbl_files_count.setText("Файлы не выбраны" if is_ru else "No files selected")
            self.btn_remove_file.setText("Убрать выбранное" if is_ru else "Remove selected")
            self.btn_remove_file.setToolTip("Убрать выделенные файлы из очереди  (Delete)" if is_ru else "Remove selected files from the queue  (Delete)")
            self.btn_clear_files.setText("Очистить список" if is_ru else "Clear list")
            self.btn_clear_files.setToolTip("Убрать все файлы из очереди (настройки сохранятся)" if is_ru else "Remove all files from the queue (settings will be kept)")
            self.files_list.setToolTip("Очередь файлов. Выделите и нажмите Delete, чтобы убрать." if is_ru else "File queue. Select items and press Delete to remove them.")
            if self.lbl_input_folder.text().startswith("Папка не выбрана") or self.lbl_input_folder.text().startswith("Folder not selected"):
                self.lbl_input_folder.setText("Папка не выбрана" if is_ru else "Folder not selected")
            self.drop_hint.setText("Перетащите сюда файлы или папки  ·  либо нажмите «Выбрать файлы»" if is_ru else "Drop files or folders here  ·  or click 'Choose files'")
            format_labels = {
                "txt": ("Текст (.txt)", "Text (.txt)"),
                "txt_timecodes": ("Таймкоды (_timecodes.txt)", "Timecodes (_timecodes.txt)"),
                "txt_diarize": ("Диаризация (_diarize.txt)", "Diarization (_diarize.txt)"),
                "txt_diarize_timecodes": ("Диар.+тайм. (_diarize_timecodes.txt)", "Diarization+timecodes (_diarize_timecodes.txt)"),
                "md": ("Markdown (.md)", "Markdown (.md)"),
                "srt": ("SRT (.srt)", "SRT (.srt)"),
                "vtt": ("VTT (.vtt)", "VTT (.vtt)"),
            }
            for fmt, cb in self.format_checkboxes.items():
                ru_label, en_label = format_labels.get(fmt, (cb.text(), cb.text()))
                cb.setText(ru_label if is_ru else en_label)
            self.cb_subtitle_sentence_split.setText(
                "Разбивать по предложениям" if is_ru else "Split by sentences"
            )
            self.lbl_subtitle_max_lines.setText(
                "Строк:" if is_ru else "Lines:"
            )
            self.lbl_subtitle_max_width.setText(
                "Символов:" if is_ru else "Characters:"
            )
        if hasattr(self, "grp_llm_source"):
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
            self.lbl_llm_status.setText("Готово к LLM-обработке" if is_ru else "Ready for LLM processing")
            if hasattr(self, "llm_drop_hint"):
                self.llm_drop_hint.setText("Перетащите сюда транскрипты  ·  либо нажмите «Выбрать транскрипты»" if is_ru else "Drop transcripts here  ·  or click 'Choose transcripts'")
            if hasattr(self, "btn_remove_llm_file"):
                self.btn_remove_llm_file.setText("Убрать выбранное" if is_ru else "Remove selected")
            if hasattr(self, "btn_clear_llm_files"):
                self.btn_clear_llm_files.setText("Очистить список" if is_ru else "Clear list")
            if hasattr(self, "llm_files_list"):
                self.llm_files_list.setToolTip("Список транскриптов. Выделите и нажмите Delete, чтобы убрать." if is_ru else "Transcript list. Select items and press Delete to remove them.")
            self.lbl_llm_files.setText("Файлы не выбраны" if is_ru and not self.transcript_files_for_llm else ("No files selected" if not is_ru and not self.transcript_files_for_llm else self.lbl_llm_files.text()))
            if hasattr(self, "lbl_llm_files_count") and not self.transcript_files_for_llm:
                self.lbl_llm_files_count.setText("Файлы не выбраны" if is_ru else "No files selected")
            self.txt_llm_transcript.setPlaceholderText(
                "Вставьте сюда транскрипт, если не хотите выбирать файлы"
                if is_ru else
                "Paste transcript here if you do not want to choose files"
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
            if hasattr(self, "btn_log_copy"):
                self.btn_log_copy.setText("Копировать" if is_ru else "Copy")
                self.btn_log_copy.setToolTip("Скопировать весь журнал в буфер обмена" if is_ru else "Copy the entire log to the clipboard")
                self.btn_log_save.setText("Сохранить…" if is_ru else "Save…")
                self.btn_log_save.setToolTip("Сохранить журнал в текстовый файл" if is_ru else "Save the log to a text file")
                self.btn_log_clear.setText("Очистить журнал" if is_ru else "Clear log")
                self.btn_log_clear.setToolTip("Очистить только журнал, не сбрасывая настройки" if is_ru else "Clear only the log without resetting settings")
            if hasattr(self, "_llm_settings_dialog"):
                self._llm_settings_dialog.setWindowTitle("Настройки LLM" if is_ru else "LLM settings")
                self.grp_llm_api_settings.setTitle("LLM API")
                self.llm_provider_labels["provider"].setText("Провайдер:" if is_ru else "Provider:")
                self.llm_provider_labels["model"].setText("Модель:" if is_ru else "Model:")
                self.llm_provider_items[5] = "Другое" if is_ru else "Other"
                current_provider = self._normalize_llm_provider(self.combo_llm_provider.currentText())
                self.combo_llm_provider.blockSignals(True)
                self.combo_llm_provider.setItemText(5, self.llm_provider_items[5])
                self.combo_llm_provider.setCurrentText(self.llm_provider_items[5] if current_provider == "Other" else current_provider)
                self.combo_llm_provider.blockSignals(False)
                self.llm_provider_labels["claude_path"].setText("Claude Code путь:" if is_ru else "Claude Code path:")
                self.llm_provider_labels["claude_args"].setText("Claude доп. аргументы:" if is_ru else "Claude extra args:")
                self.entry_llm_claude_args.setPlaceholderText("например: --permission-mode bypassPermissions" if is_ru else "example: --permission-mode bypassPermissions")
                self.llm_provider_labels["codex_path"].setText("Codex путь:" if is_ru else "Codex path:")
                self.llm_provider_labels["codex_args"].setText("Codex доп. аргументы:" if is_ru else "Codex extra args:")
                self.entry_llm_codex_args.setPlaceholderText("например: --dangerously-bypass-approvals-and-sandbox" if is_ru else "example: --dangerously-bypass-approvals-and-sandbox")
                self.llm_provider_labels["opencode_path"].setText("OpenCode путь:" if is_ru else "OpenCode path:")
                self.llm_provider_labels["opencode_args"].setText("OpenCode доп. аргументы:" if is_ru else "OpenCode extra args:")
                self.entry_llm_opencode_args.setPlaceholderText("например: --print" if is_ru else "example: --print")
                self.llm_provider_labels["pi_path"].setText("Pi путь:" if is_ru else "Pi path:")
                self.llm_provider_labels["pi_provider"].setText("Pi provider:" if is_ru else "Pi provider:")
                self.llm_provider_labels["pi_args"].setText("Pi доп. аргументы:" if is_ru else "Pi extra args:")
                self.entry_llm_pi_args.setPlaceholderText("например: --no-tools --thinking low" if is_ru else "example: --no-tools --thinking low")
                self.llm_provider_labels["other_path"].setText("Команда:" if is_ru else "Command:")
                self.llm_provider_labels["other_args"].setText("Аргументы:" if is_ru else "Arguments:")
                self.entry_llm_other_path.setPlaceholderText("путь к CLI, например my-llm" if is_ru else "CLI path, for example my-llm")
                self.entry_llm_other_args.setPlaceholderText("аргументы; промпт будет добавлен в конец как последний параметр" if is_ru else "arguments; the prompt will be appended as the last argument")
                self.prompts_group.setTitle("Готовые промпты" if is_ru else "Ready prompts")
                self.lbl_llm_summary_prompt.setText("Промпт для выжимки:" if is_ru else "Prompt for summary:")
                self.lbl_llm_tasks_prompt.setText("Промпт для задач:" if is_ru else "Prompt for tasks:")
                self.lbl_llm_custom_prompt.setText("Свой промпт:" if is_ru else "Custom prompt:")
                self.lbl_llm_settings_note.setText("Можно использовать OpenAI-compatible API, Anthropic Messages API, а также локальные Claude Code / Codex / OpenCode / Pi. Для API режим сам определяет тип API по URL или endpoint. Выбранный провайдер, модель, temperature, чекбоксы, prompt и файлы сохраняются между запусками. API Key лучше хранить в .env." if is_ru else "You can use an OpenAI-compatible API, Anthropic Messages API, or local Claude Code / Codex / OpenCode / Pi. In API mode, the app auto-detects the API type from the URL or endpoint. The selected provider, model, temperature, checkboxes, prompts, and files are saved between launches. It is best to store the API key in .env.")
                self._llm_settings_buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить" if is_ru else "Save")
                self._llm_settings_buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть" if is_ru else "Close")
                self._update_llm_provider_fields(self.combo_llm_provider.currentText())
        if hasattr(self, "_menu_file"):
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
                self._act_asr_model.setStatusTip("Выбрать модель GigaAM" if is_ru else "Select the GigaAM model")
            self._act_asr_backend.setText("Движок распознавания…" if is_ru else "Recognition engine...")
            self._act_device.setText("Устройство (CPU / GPU)…" if is_ru else "Device (CPU / GPU)…")
            self._act_llm.setText("LLM API…")
            self._act_about.setText("О программе" if is_ru else "About")

    def _translate_runtime_text(self, message: str) -> str:
        if self._lang == "ru" or not message:
            return message
        translated = str(message)
        replacements = [
            ("Подробности — на вкладке «Журнал обработки».", "See details in the 'Processing log' tab."),
            ("Не удалось сохранить журнал:\n", "Failed to save the log:\n"),
            ("Журнал скопирован в буфер обмена", "Log copied to clipboard"),
            ("Журнал сохранён: ", "Log saved: "),
            ("Журнал сохранён в ", "Log saved to "),
            ("Журнал очищен", "Log cleared"),
            ("Диаризация спикеров: ВКЛЮЧЕНА", "Speaker diarization: ENABLED"),
            ("Диаризация спикеров: ВЫКЛЮЧЕНА", "Speaker diarization: DISABLED"),
            ("Токен сохранён: ", "Token saved: "),
            ("Количество спикеров: автоопределение", "Speaker count: auto-detect"),
            ("Количество спикеров: ", "Speaker count: "),
            ("Обработка отменена пользователем", "Processing cancelled by user"),
            ("=== ОБРАБОТКА ЗАВЕРШЕНА ===", "=== PROCESSING FINISHED ==="),
            ("Общее время обработки: ", "Total processing time: "),
            (", с ошибками: ", ", with errors: "),
            ("Успешно: ", "Successful: "),
            ("Отменено. Обработано ", "Cancelled. Processed "),
            ("Готово с ошибками: ", "Completed with errors: "),
            (" успешно за ", " successful in "),
            ("Завершено за ", "Completed in "),
            ("Не удалось: ", "Failed: "),
            (" и ещё ", " and "),
            ("Критическая ошибка: ", "Critical error: "),
            ("Ошибка: ", "Error: "),
            ("Не удалось загрузить модель", "Failed to load model"),
            ("Анализ файлов и оценка времени обработки...", "Analyzing files and estimating processing time..."),
            ("Обработка ", "Processing "),
            (" файлов…", " files…"),
            ("Файл ", "File "),
            ("Подготовка…", "Preparing…"),
            ("Конвертация…", "Converting…"),
            ("Распознавание речи…", "Speech recognition…"),
            ("Распознавание речи (GigaAM-v3)...", "Speech recognition (GigaAM-v3)..."),
            ("Транскрибация завершена. Получено сегментов: ", "Transcription finished. Segments received: "),
            ("Пример структуры сегмента: keys=", "Example segment structure: keys="),
            ("Применение диаризации спикеров...", "Applying speaker diarization..."),
            ("Диаризация завершена. Найдено спикеров: ", "Diarization finished. Speakers found: "),
            ("Найдено сегментов речи: ", "Speech segments found: "),
            ("Сохранено символов: ", "Characters saved: "),
            ("Сохранено: ", "Saved: "),
            ("Время обработки: ", "Processing time: "),
            ("Конверсия: ", "Conversion: "),
            ("Транскрибация: ", "Transcription: "),
            ("Длительность: ", "Duration: "),
            ("неизвестна", "unknown"),
            ("ошибка определения длительности", "duration detection error"),
            ("длительность неизвестна", "duration unknown"),
            ("Ошибка при обработке файла ", "Error while processing file "),
            ("ОШИБКА при транскрибации: ", "TRANSCRIPTION ERROR: "),
            ("ОШИБКА VAD: ", "VAD ERROR: "),
            ("ПРЕДУПРЕЖДЕНИЕ: ", "WARNING: "),
            ("Ошибка при обработке ", "Error while processing "),
            ("Возможные причины:", "Possible reasons:"),
            ("Проверьте токен HF_TOKEN в .env файле и убедитесь, что приняли условия доступа:", "Check the HF_TOKEN in the .env file and make sure you accepted the access terms:"),
            ("Проверьте токен HF_TOKEN в src/config.py и убедитесь, что приняли условия доступа:", "Check the HF_TOKEN in src/config.py and make sure you accepted the access terms:"),
            ("Диаризация требует токен HuggingFace.", "Diarization requires a HuggingFace token."),
            ("Установите токен через чекбокс 'Диаризация' в интерфейсе.", "Set the token via the 'Diarization' checkbox in the interface."),
            ("Продолжаем без диаризации...", "Continuing without diarization..."),
            ("Спикер №", "Speaker №"),
        ]
        for old, new in replacements:
            translated = translated.replace(old, new)
        return translated
