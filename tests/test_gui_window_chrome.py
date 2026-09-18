"""The 2.0 redesign drew a mock macOS window (traffic-light dots, a fake title
bar, an inset rounded frame) inside the real window, so Windows users saw two
title bars and a mock-up-looking shell (issue #54). The app must fill its
window like a normal application."""

import os
import sys
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QFrame, QLabel  # noqa: E402

sys.modules.setdefault("gigaam", types.SimpleNamespace(load_model=lambda *args, **kwargs: object()))
sys.modules.setdefault("yt_dlp", types.SimpleNamespace(YoutubeDL=object))

from src.gui.app_qt import GigaTranscriberQtApp  # noqa: E402


@pytest.fixture
def window(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGAAM_CONFIG_DIR", str(tmp_path / "config"))
    app = QApplication.instance() or QApplication([])
    instance = GigaTranscriberQtApp()
    instance.resize(968, 850)
    instance.show()
    app.processEvents()
    yield instance
    instance.close()


def test_no_fake_macos_window_chrome(window):
    names = {w.objectName() for w in window.findChildren(QFrame) + window.findChildren(QLabel)}
    assert "window_chrome" not in names and "window_dot" not in names and "window_title" not in names


def test_app_frame_fills_the_central_widget(window):
    central = window.centralWidget()
    assert central.layout().contentsMargins().left() == 0
    assert window._app_window.geometry() == central.rect()
    app = QApplication.instance()
    window.resize(1500, 900)
    app.processEvents()
    assert central.layout().contentsMargins().left() == 0  # responsive layout must not bring the inset back


def test_language_and_theme_switches_live_in_the_sidebar(window):
    for button in (window._btn_lang, window._btn_theme):
        assert button.isEnabled() and button.isVisible()
        parent, inside_sidebar = button.parent(), False
        while parent is not None:
            inside_sidebar = inside_sidebar or parent is window._sidebar
            parent = parent.parent()
        assert inside_sidebar, button.objectName()
    assert window._btn_lang.text() in {"EN", "RU"}
    before = window._theme
    window._btn_theme.click()
    assert window._theme != before
