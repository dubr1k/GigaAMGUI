"""Примитивы оформления: палитра цветов, масштаб UI, шрифты, метрики для окна.

Mixin: методы работают со `self` главного окна. Поведение сохранено 1:1.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QFontDatabase, QFontMetrics
from PyQt6.QtWidgets import QApplication, QLabel

_BASE_FONT_PT = 12.0
_MIN_UI_SCALE = 0.85
_MAX_UI_SCALE = 1.75


def _read_ui_scale() -> float:
    raw_value = os.getenv("GIGAAM_UI_SCALE", "1").strip().replace(",", ".")
    try:
        scale = float(raw_value)
    except ValueError:
        scale = 1.0
    return max(_MIN_UI_SCALE, min(_MAX_UI_SCALE, scale))


def _format_css_number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


class StyleMixin:
    def _colors(self):
        return self._DARK if self._theme == "dark" else self._LIGHT

    def _effective_ui_scale(self) -> float:
        app = QApplication.instance()
        font_pt = app.font().pointSizeF() if app else _BASE_FONT_PT
        if font_pt <= 0:
            font_pt = _BASE_FONT_PT
        font_scale = max(_MIN_UI_SCALE, min(_MAX_UI_SCALE, font_pt / _BASE_FONT_PT))
        ui_scale = font_scale * _read_ui_scale()
        return round(max(_MIN_UI_SCALE, min(_MAX_UI_SCALE, ui_scale)), 4)

    def _px(self, value: int | float) -> int:
        return max(1, int(round(value * self._ui_scale)))

    def _pt(self, value: int | float) -> float:
        return round(value * self._ui_scale, 2)

    def _pt_css(self, value: int | float) -> str:
        return _format_css_number(self._pt(value))

    def _transparent_label_style(
        self,
        color: str,
        font_pt: int | float | None = None,
        font_weight: str | None = None,
    ) -> str:
        parts = ["background: transparent", f"color: {color}"]
        if font_pt is not None:
            parts.append(f"font-size: {self._pt_css(font_pt)}pt")
        if font_weight:
            parts.append(f"font-weight: {font_weight}")
        return "; ".join(parts) + ";"

    def _font(self, point_size: int | float, weight: QFont.Weight = QFont.Weight.Normal, fixed: bool = False) -> QFont:
        font_kind = QFontDatabase.SystemFont.FixedFont if fixed else QFontDatabase.SystemFont.GeneralFont
        font = QFontDatabase.systemFont(font_kind)
        font.setPointSizeF(self._pt(point_size))
        font.setWeight(weight)
        return font

    def _tab_min_width(self, labels: tuple[str, ...]) -> int:
        metrics = QFontMetrics(self._font(11, QFont.Weight.Bold))
        text_width = max(metrics.horizontalAdvance(label) for label in labels)
        return text_width + self._px(36)

    def _label_column_width(self, labels: tuple[str, ...], extra: int = 6) -> int:
        """Ширина колонки, достаточная для самого длинного лейбла из набора.

        Используется, чтобы поля формы (значения справа от лейблов) всегда
        начинались с одной и той же координаты X, даже если тексты лейблов
        разной длины ("Провайдер:", "Модель:", "API URL:" и т.д.).
        """
        metrics = QFontMetrics(self._font(10))
        text_width = max(metrics.horizontalAdvance(label) for label in labels)
        return text_width + self._px(extra)

    def _form_label(self, text: str, column_width: int) -> QLabel:
        lbl = QLabel(text)
        lbl.setFixedWidth(column_width)
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return lbl

    def _toggle_theme(self):
        self._theme = "dark" if self._theme == "light" else "light"
        self.user_settings.settings["theme"] = self._theme
        self.user_settings._save_settings()
        self._apply_theme()
        self._btn_theme.setText(self._colors()["theme_btn"])
