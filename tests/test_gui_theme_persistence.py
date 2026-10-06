import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

sys.modules.setdefault("gigaam", types.SimpleNamespace(load_model=lambda *args, **kwargs: object()))
sys.modules.setdefault("yt_dlp", types.SimpleNamespace(YoutubeDL=object))

from src.gui.app_qt import GigaTranscriberQtApp  # noqa: E402


def test_saved_dark_theme_is_applied_on_startup(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GIGAAM_CONFIG_DIR", str(tmp_path / "config"))
    app = QApplication.instance() or QApplication([])
    first = GigaTranscriberQtApp()
    first.user_settings.set_value("theme", "dark")
    first.close()

    restored = GigaTranscriberQtApp()
    try:
        assert restored._theme == "dark"
        assert restored._colors()["bg"] in restored.styleSheet()
        assert restored._colors()["input_bg"] in restored.styleSheet()
    finally:
        restored.close()
        app.processEvents()
