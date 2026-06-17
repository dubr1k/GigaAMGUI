import os
import threading

from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from src.gui.app_qt import GigaTranscriberQtApp


def _autoclose(window, monkeypatch):
    """Закрытие окна во время активной загрузки/обработки теперь спрашивает
    подтверждение — в тестах автоматически отвечаем «Да», чтобы не блокироваться
    на модальном диалоге."""
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )
    window.close()


def test_file_progress_still_accepts_integer():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = GigaTranscriberQtApp()

    window._update_file_progress(50)

    assert window.progress_bar_file.value() == 50
    window.close()


def test_content_in_scroll_area_no_overlap_when_short():
    # Регрессия: при низком окне контент не должен наезжать — он в QScrollArea
    from PyQt6.QtWidgets import QScrollArea
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = GigaTranscriberQtApp()
    window.resize(1000, 640)
    window.show()
    app.processEvents()

    assert isinstance(window.centralWidget(), QScrollArea)
    content = window.centralWidget().widget()
    log_bottom = window.log_text.mapTo(content, window.log_text.rect().bottomLeft()).y()
    clear_top = window.btn_clear.mapTo(content, window.btn_clear.rect().topLeft()).y()
    assert log_bottom <= clear_top  # нет вертикального наложения
    window.close()


def test_download_progress_updates_bar():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = GigaTranscriberQtApp()
    window._update_download_progress(37)
    assert window.progress_upload.value() == 37
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
    _autoclose(window, monkeypatch)
