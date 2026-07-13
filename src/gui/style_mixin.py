"""Примитивы оформления: палитра цветов, масштаб UI, шрифты, метрики для окна.

Mixin: методы работают со `self` главного окна. Поведение сохранено 1:1.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics
from PyQt6.QtWidgets import QApplication, QColorDialog, QLabel

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


def _mix_colors(color1: str, color2: str, ratio: float) -> str:
    ratio = max(0.0, min(1.0, ratio))
    c1 = QColor(color1)
    c2 = QColor(color2)
    r = round(c1.red() * (1.0 - ratio) + c2.red() * ratio)
    g = round(c1.green() * (1.0 - ratio) + c2.green() * ratio)
    b = round(c1.blue() * (1.0 - ratio) + c2.blue() * ratio)
    return QColor(r, g, b).name()


class StyleMixin:
    def _colors(self):
        base = dict(self._DARK if self._theme == "dark" else self._LIGHT)
        accent = str(self.user_settings.get_value("accent_color", "") or "").strip()
        if not accent:
            return base
        accent_color = QColor(accent)
        if not accent_color.isValid():
            return base

        accent = accent_color.name()
        base["accent"] = accent
        base["accent2"] = accent_color.darker(112 if self._theme == "light" else 118).name()
        base["accent3"] = accent_color.darker(128 if self._theme == "light" else 138).name()
        base["accent_dis"] = _mix_colors(accent, "#ffffff" if self._theme == "light" else base["bg"], 0.58)
        base["btn_hover_border"] = accent
        base["btn_hover_text"] = _mix_colors(accent, "#000000" if self._theme == "light" else "#ffffff", 0.18)
        base["progress_chunk"] = accent
        base["progress_chunk2"] = base["accent2"]
        base["tab_accent"] = accent
        base["input_sel"] = _mix_colors(accent, "#ffffff" if self._theme == "light" else "#000000", 0.72 if self._theme == "light" else 0.55)
        return base

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

    def _choose_accent_color(self):
        current = QColor(self._colors()["accent"])
        title = self._t("Выберите акцентный цвет", "Choose accent color")
        color = QColorDialog.getColor(current, self, title)
        if not color.isValid():
            return
        self.user_settings.set_value("accent_color", color.name())
        self._apply_theme()
        self._set_status(self._t("Акцентный цвет изменён", "Accent color changed"))

    def _reset_accent_color(self):
        if "accent_color" not in self.user_settings.settings:
            return
        self.user_settings.settings.pop("accent_color", None)
        self.user_settings._save_settings()
        self._apply_theme()
        self._set_status(self._t("Акцентный цвет сброшен", "Accent color reset"))
