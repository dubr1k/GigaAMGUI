"""Тема: одна палитра на тему и один стиль шкал прогресса."""

import os
import sys
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

sys.modules.setdefault("gigaam", types.SimpleNamespace(load_model=lambda *args, **kwargs: object()))
sys.modules.setdefault("yt_dlp", types.SimpleNamespace(YoutubeDL=object))

from src.gui.app_qt import GigaTranscriberQtApp  # noqa: E402


@pytest.fixture
def window(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGAAM_CONFIG_DIR", str(tmp_path / "config"))
    app = QApplication.instance() or QApplication([])
    instance = GigaTranscriberQtApp()
    yield instance
    instance.close()
    app.processEvents()


def _bars(window):
    return {
        "total": window.progress_bar_total,
        "file": window.progress_bar_file,
        "llm": window.progress_bar_llm,
    }


def test_theme_button_offers_the_other_theme(window):
    window._theme = "light"
    window._apply_theme()
    light = window._btn_theme.text()
    window._toggle_theme()
    dark = window._btn_theme.text()

    # Светлая палитра 2.0 переопределяла иконку на «☀»: солнце в обеих темах.
    assert light == "🌙"
    assert dark == "☀️"


def test_theme_change_keeps_pill_shaped_vertical_gradient_bars(window):
    for theme in ("light", "dark"):
        window._theme = theme
        window._apply_theme()
        colors = window._colors()
        for name, bar in _bars(window).items():
            sheet = bar.styleSheet()
            assert f"border-radius: {bar.height() // 2}px" in sheet, (theme, name, sheet)
            assert "x1:0,y1:0,x2:0,y2:1" in sheet, (theme, name, sheet)
            assert colors["progress_bg"] in sheet, (theme, name, sheet)
