"""Вкладка API: проверка сервиса не блокирует окно, примеры кода следуют теме."""

import os
import sys
import time
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

sys.modules.setdefault("gigaam", types.SimpleNamespace(load_model=lambda *args, **kwargs: object()))
sys.modules.setdefault("yt_dlp", types.SimpleNamespace(YoutubeDL=object))

from src.gui import api_surface_mixin  # noqa: E402
from src.gui.app_qt import GigaTranscriberQtApp  # noqa: E402


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


@pytest.fixture(autouse=True)
def _isolated_gui_config(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGAAM_CONFIG_DIR", str(tmp_path / "config"))


def _pump_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_api_health_probe_runs_off_the_qt_thread(monkeypatch):
    app = QApplication.instance() or QApplication([])

    def slow_health(url, timeout):
        time.sleep(1.0)
        return _Response()

    monkeypatch.setattr(api_surface_mixin, "urlopen", slow_health)
    window = GigaTranscriberQtApp()
    try:
        started = time.monotonic()
        window._refresh_api_status()
        assert time.monotonic() - started < 1.0, "the health probe blocked the window"
        assert _pump_until(lambda: "Запущен" in window.api_status_label.text() or "Running" in window.api_status_label.text())
        assert window.api_docs_button.isEnabled()
    finally:
        window.close()
        app.processEvents()


def test_api_code_samples_follow_the_dark_theme(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(api_surface_mixin, "urlopen", lambda *_a, **_k: _Response())
    window = GigaTranscriberQtApp()
    try:
        window._theme = "dark"
        window._apply_theme()
        window.resize(window._px(1040), window._px(900))
        window.show()
        window._show_tab("api")
        app.processEvents()
        editor = next(iter(window.api_code_edits.values()))
        image = editor.viewport().grab().toImage()
        background = image.pixelColor(image.width() - 5, image.height() - 5)
        assert background.lightness() < 100, background.name()
    finally:
        window.close()
        app.processEvents()
