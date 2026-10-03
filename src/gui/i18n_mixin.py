"""Локализация интерфейса (RU/EN) и перевод runtime-сообщений для GigaTranscriberQtApp.

Сами подписи переводят методы _retranslate_* рядом с построением каждой
поверхности; здесь — переключение языка, диспетчер _apply_language, _t и
перевод runtime-сообщений журнала.

Mixin: методы работают со `self` главного окна.
"""
from __future__ import annotations

from PyQt6.QtCore import QLibraryInfo, QTranslator
from PyQt6.QtWidgets import QApplication

from ..core.log_i18n import translate_log


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

    def _bilingual(self, apply, ru: str, en: str) -> None:
        """Поставить подпись сейчас и повторять при каждой смене языка.

        apply — сеттер постоянного виджета (label.setText, button.setToolTip,
        lambda text: combo.setItemText(0, text)…). Для статичных подписей
        страниц это избавляет от отдельной строки в _retranslate_*.
        """
        if not hasattr(self, "_bilingual_texts"):
            self._bilingual_texts = []
        self._bilingual_texts.append((apply, ru, en))
        apply(self._t(ru, en))

    def _retranslate_known(self, apply, current: str, pairs) -> None:
        """Перевести меняющуюся подпись, только если в ней сейчас один из pairs.

        Статус идущей записи или текст ошибки по таблице не переводятся и
        остаются как есть до следующего обновления.
        """
        for ru, en in pairs:
            if current in (ru, en):
                apply(self._t(ru, en))
                return

    def _retranslate_bilingual(self, is_ru: bool) -> None:
        for apply, ru, en in getattr(self, "_bilingual_texts", []):
            apply(ru if is_ru else en)

    def _normalize_llm_provider(self, provider: str) -> str:
        return "Other" if provider in {"Другое", "Other"} else provider

    def _apply_language(self):
        """Перевести интерфейс: каждая поверхность переводит свои виджеты сама.

        _retranslate_* живут рядом с построением своей поверхности, чтобы новый
        виджет и его перевод правились в одном модуле.
        """
        is_ru = self._lang == "ru"
        _install_qt_translator(QApplication.instance(), self._lang)
        for retranslate in (
            self._retranslate_bilingual,
            self._retranslate_shell,
            self._retranslate_processing_page,
            self._retranslate_processing_options,
            self._retranslate_llm_page,
            self._retranslate_journal,
            self._retranslate_llm_settings_dialog,
            self._retranslate_live_tab,
            self._retranslate_result_page,
            self._retranslate_api_tab,
            self._retranslate_menu,
        ):
            retranslate(is_ru)
        self._sync_support_surface_settings()

    def _translate_runtime_text(self, message: str) -> str:
        if self._lang == "ru" or not message:
            return message
        # Строки журнала обработки переводятся общей таблицей (src.core.log_i18n);
        # ниже — только статусы и подписи самого PyQt-окна.
        translated = translate_log(str(message))
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
            ("Анализ файлов и оценка времени обработки...", "Analyzing files and estimating processing time..."),
            ("Обработка ", "Processing "),
            (" файлов…", " files…"),
            ("Подготовка…", "Preparing…"),
            ("Конвертация…", "Converting…"),
            ("неизвестна", "unknown"),
            ("ошибка определения длительности", "duration detection error"),
            ("длительность неизвестна", "duration unknown"),
            ("Диаризация требует токен HuggingFace.", "Diarization requires a HuggingFace token."),
            ("Продолжаем без диаризации...", "Continuing without diarization..."),
            ("Спикер №", "Speaker №"),
        ]
        for old, new in replacements:
            translated = translated.replace(old, new)
        return translated
