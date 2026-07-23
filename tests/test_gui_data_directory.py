import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PyQt6 = pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

from src.gui import settings_mixin  # noqa: E402
from src.gui.app_qt import GigaTranscriberQtApp  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_gui_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGAAM_CONFIG_DIR", str(tmp_path / "config"))


def test_settings_menu_exposes_retranslated_data_directory_action():
    app = QApplication.instance() or QApplication([])
    window = GigaTranscriberQtApp()

    assert "Папка данных" in window._act_data_dir.text()
    window._lang = "en"
    window._apply_language()
    assert window._act_data_dir.text() == "Data and model directory…"

    window.close()
    app.processEvents()


def test_gui_persists_selected_data_directory_and_requires_restart(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = GigaTranscriberQtApp()
    selected = tmp_path / "large-disk"
    selected.mkdir()
    saved = []
    shown = []

    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        lambda *_args, **_kwargs: str(selected),
    )
    monkeypatch.setattr(settings_mixin, "save_data_dir_selection", saved.append)
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *_args, **_kwargs: shown.append(_args[2]),
    )

    window._select_data_directory()

    assert saved == [str(selected)]
    assert any("Перезапустите" in message for message in shown)

    window.close()
    app.processEvents()
