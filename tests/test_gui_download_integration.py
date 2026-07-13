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


def test_hf_token_button_always_opens_token_dialog(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = GigaTranscriberQtApp()
    opened = []

    monkeypatch.setenv("HF_TOKEN", "hf_existing_token")
    monkeypatch.setattr(window, "_show_hf_token_dialog", lambda: opened.append(True) or True)

    window.btn_hf_token.click()

    assert opened == [True]
    window.close()


def test_hf_token_button_is_retranslated():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = GigaTranscriberQtApp()

    window._lang = "en"
    window._apply_language()

    assert window.btn_hf_token.text() == "Set / change HF token"
    window.close()


def test_gui_does_not_pin_windows_font_families():
    source = Path("src/gui/app_qt.py").read_text(encoding="utf-8")
    assert 'QFont("Arial"' not in source
    assert 'QFont("Consolas"' not in source


def test_ui_scale_env_is_clamped(monkeypatch):
    from src.gui import style_mixin

    monkeypatch.setenv("GIGAAM_UI_SCALE", "1.25")
    assert style_mixin._read_ui_scale() == 1.25

    monkeypatch.setenv("GIGAAM_UI_SCALE", "0.2")
    assert style_mixin._read_ui_scale() == style_mixin._MIN_UI_SCALE

    monkeypatch.setenv("GIGAAM_UI_SCALE", "9")
    assert style_mixin._read_ui_scale() == style_mixin._MAX_UI_SCALE


def _gui_source() -> str:
    # GUI разбит на app_qt.py + *_mixin.py; QSS/тема/построение UI лежат в разных
    # модулях. Читаем весь пакет src/gui, чтобы source-scrape находил правила
    # независимо от того, в каком модуле они сейчас определены.
    parts = [p.read_text(encoding="utf-8") for p in sorted(Path("src/gui").glob("*.py"))]
    return "\n".join(parts)


def test_gui_text_widgets_have_transparent_backgrounds():
    source = _gui_source()
    assert re.search(r"QLabel \{\{\s+background: transparent;", source)
    assert re.search(r"QCheckBox \{\{\s+background: transparent;", source)
    group_title_block = source.split("QGroupBox::title {{", 1)[1].split("            }}", 1)[0]
    assert "background: transparent;" in group_title_block
    assert "background-color:" not in group_title_block


def test_gui_tabs_do_not_elide_labels():
    source = _gui_source()
    tab_block = source.split("QTabBar::tab {{", 1)[1].split("            }}", 1)[0]
    assert "min-width: {tab_min_width}px;" in tab_block
    assert "setElideMode(Qt.TextElideMode.ElideNone)" in source


def test_stage_aware_progress():
    from src.core.progress import ProgressEvent

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = GigaTranscriberQtApp()
    window.is_processing = True
    window.total_files = 2
    window.files_processed = 0
    window.files_to_process = ["/tmp/a.mp3", "/tmp/b.mp3"]
    window.file_estimates = {"/tmp/a.mp3": 100, "/tmp/b.mp3": 100}

    # Этап конвертации с детерминированным прогрессом через ProgressEvent.
    window._on_stage_update(ProgressEvent(
        stage="conversion",
        stage_progress=0.0,
        file_progress=0.0,
    ))
    assert "Конвертация" in window.lbl_stage.text()
    assert window.progress_bar_file.value() <= 15
    assert window.lbl_file_counter.text() == "Файл 1 / 2"

    # Переключение на распознавание — в UI должно стать событие детерминированной стадии.
    window._on_stage_update(ProgressEvent(
        stage="transcription",
        stage_progress=0.5,
        file_progress=0.5,
    ))
    assert "Распознавание" in window.lbl_stage.text()
    assert window.progress_bar_file.value() >= 40

    # Диаризация может быть indeterminate (нет реального завершенного прогресса).
    window._on_stage_update(ProgressEvent(
        stage="diarization",
        stage_progress=None,
        file_progress=0.7,
    ))
    assert window.progress_bar_file.minimum() == 0
    assert window.progress_bar_file.maximum() == 0

    # Общий прогресс должен учитывать долю текущего файла без таймерных приближений.
    assert window.progress_bar_total.value() == 35

    # Новый файл обязан сбросить монотонный guard предыдущего файла.
    window.current_stage_file_progress = 1.0
    window.files_processed = 1
    window._update_current_file_info("b.mp3")
    window._on_stage_update(ProgressEvent(
        stage="preparing",
        stage_progress=0.0,
        file_progress=0.0,
    ))
    assert window.current_stage_file_progress == 0.0
    assert window.progress_bar_total.value() == 50
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


_APP = None


def _new_window():
    global _APP
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _APP = QApplication.instance() or QApplication([])
    return GigaTranscriberQtApp()


def test_file_queue_list_reflects_and_removes():
    window = _new_window()
    # Пустое состояние: показывается подсказка, список скрыт
    assert window.drop_hint.isHidden() is False
    assert window.files_list.isHidden() is True

    window.files_to_process = ["/tmp/a.mp3", "/tmp/b.mp3", "/tmp/c.mp3"]
    window._refresh_files_list()
    assert window.files_list.count() == 3
    assert window.files_list.isHidden() is False
    assert window.drop_hint.isHidden() is True
    assert "3" in window.lbl_files_count.text()

    # Убираем один файл по выделению
    window.files_list.item(1).setSelected(True)
    window._remove_selected_files()
    assert window.files_to_process == ["/tmp/a.mp3", "/tmp/c.mp3"]
    assert window.files_list.count() == 2

    # Полная очистка списка
    window._clear_files_list()
    assert window.files_to_process == []
    assert window.files_list.isHidden() is True
    assert window.drop_hint.isHidden() is False
    window.close()


def test_speakers_spinbox_auto_value():
    window = _new_window()
    assert window.entry_num_speakers.value() == 0
    assert window.entry_num_speakers.specialValueText() == "Авто"
    window.entry_num_speakers.setValue(3)
    assert window.entry_num_speakers.value() == 3
    window.close()


def test_cancel_button_lifecycle():
    window = _new_window()
    # Скрыта, пока нет обработки
    assert window.btn_cancel.isVisible() is False
    # Имитация активной обработки
    window.is_processing = True
    window._cancel_processing()
    assert window._cancel_requested is True
    assert window.btn_cancel.isEnabled() is False
    window.is_processing = False
    window.close()


def test_upload_bar_hidden_until_download(monkeypatch, tmp_path):
    window = _new_window()
    assert window.progress_upload.isHidden() is True

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: types.SimpleNamespace(start=lambda: None))
    window.input_dir = str(tmp_path)
    window.input_path.setText("https://example.test/video")
    window._start_download()
    assert window.progress_upload.isHidden() is False

    window._on_download_failed("boom")
    assert window.progress_upload.isHidden() is True
    window.close()


def test_open_results_folder_uses_last_dir(monkeypatch, tmp_path):
    from src.gui import settings_mixin

    window = _new_window()
    opened = {}
    monkeypatch.setattr(settings_mixin.QDesktopServices, "openUrl", lambda url: opened.setdefault("url", url))
    window._last_result_dir = str(tmp_path)
    window._open_results_folder()
    assert "url" in opened
    assert opened["url"].toLocalFile() == str(tmp_path)
    window.close()


def test_log_copy_and_clear():
    window = _new_window()
    window.log("строка журнала")
    window._copy_log()
    assert "строка журнала" in QApplication.clipboard().text()
    window._clear_log()
    assert window.log_text.toPlainText().strip() == ""
    window.close()


def test_idle_status_is_translated_when_language_changes():
    window = _new_window()
    assert window.lbl_status.text() == "Готов к работе"
    window._toggle_language()
    assert window.lbl_status.text() == "Ready to work"
    window.close()


def test_menu_bar_has_core_menus():
    window = _new_window()
    titles = [a.text() for a in window.menuBar().actions()]
    assert "Файл" in titles
    assert "Вид" in titles
    assert "Справка" in titles
    window.close()


def test_geometry_not_persisted_in_headless():
    window = _new_window()
    window.user_settings.settings.pop("window_geometry", None)
    window._save_geometry()
    assert "window_geometry" not in window.user_settings.settings
    window.close()
