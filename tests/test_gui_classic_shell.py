"""The PyQt window uses the classic shell (menu bar, header, visible tabs,
numbered sections) with the 2.0 pages as extra tabs. The 2.0 redesign drew a
sidebar and a mock macOS window inside the real one (issue #54)."""

import os
import sys
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QFrame, QLabel, QTabWidget  # noqa: E402

sys.modules.setdefault("gigaam", types.SimpleNamespace(load_model=lambda *args, **kwargs: object()))
sys.modules.setdefault("yt_dlp", types.SimpleNamespace(YoutubeDL=object))

from src.gui.app_qt import GigaTranscriberQtApp  # noqa: E402


@pytest.fixture
def window(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGAAM_CONFIG_DIR", str(tmp_path / "config"))
    app = QApplication.instance() or QApplication([])
    instance = GigaTranscriberQtApp()
    instance.show()
    app.processEvents()
    yield instance
    instance.close()


def test_classic_shell_has_menu_header_and_visible_tabs(window):
    assert window.menuBar().isVisible()
    assert window._title_label.isVisible() and window._btn_lang.isVisible() and window._btn_theme.isVisible()
    tabs = window.findChild(QTabWidget, "")
    assert window.tabs.tabBar().isVisible()
    assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == [
        "Обработка", "Live", "LLM", "API", "Журнал", "Настройки",
    ]
    assert tabs is not None


def test_no_sidebar_or_mock_window_chrome(window):
    names = {w.objectName() for w in window.findChildren(QFrame) + window.findChildren(QLabel)}
    for gone in ("sidebar", "window_chrome", "window_dot", "window_title", "app_window", "processing_settings_panel"):
        assert gone not in names, gone
    assert window.centralWidget().layout().contentsMargins().left() > 0  # plain page margins, no inset frame


def test_processing_page_keeps_numbered_sections_and_result_stack(window):
    assert window.grp_files.title().startswith("1.")
    assert window.grp_formats.title().startswith("5.")
    assert window.btn_start.text() == "ЗАПУСТИТЬ ОБРАБОТКУ"
    assert window.processing_stack.currentWidget() is window._processing_start_page
    assert window.result_file_picker is not None and window.journal_table is not None


def test_theme_toggle_recolours_palette(window):
    before = window._theme
    window._btn_theme.click()
    assert window._theme != before
    window._btn_theme.click()
    assert window._theme == before
