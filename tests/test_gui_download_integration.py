import os

from PyQt6.QtWidgets import QApplication

from src.gui.app_qt import GigaTranscriberQtApp


def test_file_progress_still_accepts_integer():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = GigaTranscriberQtApp()

    window._update_file_progress(50)

    assert window.progress_bar_file.value() == 50
    window.close()
