"""The UI scale follows the system font, but each platform's default font is a
different size (macOS 13 pt, Windows 9 pt, Linux 10 pt); with a single 12 pt
baseline Windows collapsed to the minimum scale (issue #54)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QFont  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from src.gui import style_mixin  # noqa: E402


@pytest.mark.parametrize(
    ("platform", "font_pt"),
    [("win32", 9.0), ("darwin", 13.0), ("linux", 10.0)],
)
def test_platform_default_font_yields_unit_scale(platform, font_pt):
    assert 1.0 <= style_mixin.font_scale_for(font_pt, platform) <= 1.15


def test_effective_scale_on_windows_default_font_is_not_minimum(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(style_mixin.sys, "platform", "win32")
    font = QFont(app.font())
    font.setPointSizeF(9.0)
    app.setFont(font)
    try:
        scale = style_mixin.StyleMixin._effective_ui_scale(object())
    finally:
        app.setFont(QFont())
    assert scale >= 1.0
