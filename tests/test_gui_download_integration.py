import os
import re
import sys
import threading
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

sys.modules.setdefault("gigaam", types.SimpleNamespace(load_model=lambda *args, **kwargs: object()))
sys.modules.setdefault("yt_dlp", types.SimpleNamespace(YoutubeDL=object))

from src.gui import app_qt  # noqa: E402
from src.gui.app_qt import GigaTranscriberQtApp  # noqa: E402


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


def test_processing_tab_in_scroll_area_no_overlap_when_short():
    # Регрессия: вкладка «Обработка» в QScrollArea, элементы не наезжают при низком окне
    from PyQt6.QtWidgets import QScrollArea
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = GigaTranscriberQtApp()
    window.resize(940, 560)
    window.show()
    app.processEvents()

    proc_scroll = window.tabs.widget(0)
    assert isinstance(proc_scroll, QScrollArea)
    content = proc_scroll.widget()
    start_bottom = window.btn_start.mapTo(content, window.btn_start.rect().bottomLeft()).y()
    clear_top = window.btn_clear.mapTo(content, window.btn_clear.rect().topLeft()).y()
    assert start_bottom <= clear_top  # нет вертикального наложения
    window.close()


def test_default_size_needs_no_scroll():
    # При стартовом размере вкладка «Обработка» должна помещаться без прокрутки
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = GigaTranscriberQtApp()
    window.show()
    app.processEvents()

    proc_scroll = window.tabs.widget(0)
    assert proc_scroll.verticalScrollBar().maximum() == 0
    assert proc_scroll.horizontalScrollBar().maximum() == 0
    window.close()


def test_log_is_on_second_tab():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = GigaTranscriberQtApp()
    assert window.tabs.count() == 2
    assert window.tabs.tabText(0) == "Обработка"
    assert "Журнал" in window.tabs.tabText(1)
    # Журнал лежит на второй вкладке, и логирование в него работает
    window.log("тестовое сообщение")
    assert "тестовое сообщение" in window.log_text.toPlainText()
    window.close()


def test_gui_does_not_pin_windows_font_families():
    source = Path("src/gui/app_qt.py").read_text(encoding="utf-8")
    assert 'QFont("Arial"' not in source
    assert 'QFont("Consolas"' not in source


def test_ui_scale_env_is_clamped(monkeypatch):
    monkeypatch.setenv("GIGAAM_UI_SCALE", "1.25")
    assert app_qt._read_ui_scale() == 1.25

    monkeypatch.setenv("GIGAAM_UI_SCALE", "0.2")
    assert app_qt._read_ui_scale() == app_qt._MIN_UI_SCALE

    monkeypatch.setenv("GIGAAM_UI_SCALE", "9")
    assert app_qt._read_ui_scale() == app_qt._MAX_UI_SCALE


def test_gui_text_widgets_have_transparent_backgrounds():
    source = Path("src/gui/app_qt.py").read_text(encoding="utf-8")
    assert re.search(r"QLabel \{\{\s+background: transparent;", source)
    assert re.search(r"QCheckBox \{\{\s+background: transparent;", source)
    group_title_block = source.split("QGroupBox::title {{", 1)[1].split("            }}", 1)[0]
    assert "background: transparent;" in group_title_block
    assert "background-color:" not in group_title_block


def test_stage_aware_progress():
    import time
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = GigaTranscriberQtApp()
    window.is_processing = True
    window.total_files = 2
    window.files_processed = 0
    window.files_to_process = ["/tmp/a.mp3", "/tmp/b.mp3"]
    window.file_estimates = {"/tmp/a.mp3": 100, "/tmp/b.mp3": 100}
    window.current_file_start_time = time.time()

    # Этап конвертации — прогресс в нижней «полосе» (0..15%)
    window._on_stage_update("conversion", 0.0)
    assert "Конвертация" in window.lbl_stage.text()
    assert window.progress_bar_file.value() <= 15
    assert window.lbl_file_counter.text() == "Файл 1 / 2"

    # Распознавание, ~половина оценочного времени -> около середины полосы файла
    window._on_stage_update("transcription", 0.0)
    window._stage_start_time = time.time() - 42  # ~42 из ~85 c
    window._refresh_progress()
    assert "Распознавание" in window.lbl_stage.text()
    assert 40 <= window.progress_bar_file.value() <= 75
    # Общий прогресс = доля файла / число файлов
    assert 20 <= window.progress_bar_total.value() <= 38
    window.is_processing = False  # чтобы закрытие не показывало модальный диалог
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
