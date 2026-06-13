import os
import threading

from PyQt6.QtWidgets import QFileDialog
from PyQt6.QtWidgets import QApplication

from src.gui.app_qt import GigaTranscriberQtApp


def test_file_progress_still_accepts_integer():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = GigaTranscriberQtApp()

    window._update_file_progress(50)

    assert window.progress_bar_file.value() == 50
    window.close()


def test_url_download_asks_for_download_folder(monkeypatch, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = GigaTranscriberQtApp()
    started = {}

    class FakeThread:
        def __init__(self, target, args, daemon):
            started["target"] = target
            started["args"] = args
            started["daemon"] = daemon

        def start(self):
            started["started"] = True

    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        lambda *args, **kwargs: str(tmp_path),
    )
    monkeypatch.setattr(threading, "Thread", FakeThread)

    window.input_dir = ""
    window.input_path.setText("https://example.test/video")
    window._start_download()

    assert window.input_dir == str(tmp_path)
    assert window.lbl_input_folder.text().endswith(str(tmp_path)[-60:])
    assert started["args"] == ("https://example.test/video", str(tmp_path))
    assert started["daemon"] is True
    assert started["started"] is True
    window.close()
