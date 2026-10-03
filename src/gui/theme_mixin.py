"""Применение темы (палитра Qt + таблица стилей) для GigaTranscriberQtApp.

Таблица стилей собирается чистой функцией qss.build_stylesheet; здесь — палитра
Qt и стили виджетов, которым нужен свой setStyleSheet (шкалы, подписи прогресса).

Mixin: метод _apply_theme работает со `self` главного окна и его хелперами стилей
(_colors/_px/_pt_css/...) из StyleMixin. scripts/render_gui_screenshots.py
вызывает именно _apply_theme.
"""
from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QProgressBar

from .qss import build_stylesheet


class ThemeMixin:
    def _apply_theme(self):
        c = self._colors()
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window,          QColor(c["bg"]))
        palette.setColor(QPalette.ColorRole.WindowText,      QColor(c["text"]))
        palette.setColor(QPalette.ColorRole.Base,            QColor(c["input_bg"]))
        palette.setColor(QPalette.ColorRole.AlternateBase,   QColor(c["bg_card"]))
        palette.setColor(QPalette.ColorRole.Text,            QColor(c["text"]))
        palette.setColor(QPalette.ColorRole.Button,          QColor(c["btn_bg"]))
        palette.setColor(QPalette.ColorRole.ButtonText,      QColor(c["btn_text"]))
        palette.setColor(QPalette.ColorRole.Highlight,       QColor(c["accent"]))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        self.setPalette(palette)

        # Six tabs must fit the 940 pt minimum window: size them by the
        # longest label, not by the 1.6-era "Журнал обработки".
        tab_min_width = self._tab_min_width(("Обработка", "Настройки", "Processing"))
        self.setStyleSheet(build_stylesheet(c, self._px, self._pt_css, tab_min_width=tab_min_width))
        self._style_drop_hint()
        if hasattr(self, "_btn_theme"):
            self._btn_theme.setText(c["theme_btn"])
        # Тему меняют и кнопка в заголовке, и Ctrl+T, и вкладка «Настройки».
        self._sync_support_surface_settings()

        for bar in self.findChildren(QProgressBar):
            if bar.property(self._PROGRESS_FONT_PT) is not None:
                self._style_progress_bar(bar)
        if hasattr(self, 'progress_upload'):
            self.progress_upload.setStyleSheet(
                f"QProgressBar {{ border: none; background-color: {c['progress_bg']};"
                f"  border-radius: {self._px(3)}px; }}"
                f"QProgressBar::chunk {{ background-color: {c['accent']}; border-radius: {self._px(3)}px; }}"
            )
        if hasattr(self, 'lbl_file_counter'):
            self.lbl_file_counter.setStyleSheet(
                f"color: {c['accent']}; font-size: {self._pt_css(11)}pt; font-weight: bold;"
            )
        if hasattr(self, 'lbl_status'):
            # Прозрачный фон: строка статуса сливается с карточкой, а не выглядит
            # как тёмная «вдавленная» рамка (status_bg темнее bg_card).
            self.lbl_status.setStyleSheet(
                f"color: {c['status_text']}; font-size: {self._pt_css(10)}pt; font-weight: bold;"
                f"background: transparent; padding: {self._px(2)}px;"
            )
        if hasattr(self, 'lbl_stage'):
            self.lbl_stage.setStyleSheet(
                self._transparent_label_style(c["text_sub"], font_pt=9, font_weight="600")
            )
        if hasattr(self, 'lbl_current_file'):
            self.lbl_current_file.setStyleSheet(self._transparent_label_style(c["text_mute2"], font_pt=9))
        if hasattr(self, 'lbl_overall'):
            # Стиль задаётся при построении: без этого после смены темы подпись
            # оставалась цветом прежней (светлое на светлом).
            self.lbl_overall.setStyleSheet(
                self._transparent_label_style(c["text_sub"], font_pt=11, font_weight="bold")
            )
