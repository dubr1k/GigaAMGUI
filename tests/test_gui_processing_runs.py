"""Жизненный цикл пакетной обработки: отмена, «Очистить всё» и новый запуск.

Воркер обработки — daemon-поток, который проверяет отмену только между
файлами. Поэтому всё, что сбрасывает интерфейс посреди запуска, должно
отменять именно этот запуск, а не общий флаг окна, который следующий
«Старт» тут же опустит обратно.
"""

import os
import sys
import threading
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

sys.modules.setdefault("gigaam", types.SimpleNamespace(load_model=lambda *args, **kwargs: object()))
sys.modules.setdefault("yt_dlp", types.SimpleNamespace(YoutubeDL=object))

from src.gui import processing_mixin  # noqa: E402
from src.gui.app_qt import GigaTranscriberQtApp  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_gui_config(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGAAM_CONFIG_DIR", str(tmp_path / "config"))


class _CapturedThread:
    """Поток, который не стартует: тест сам решает, когда выполнить воркер."""

    started: list = []

    def __init__(self, *, target, kwargs=None, daemon=None, **_ignored):
        self.target = target
        self.kwargs = kwargs or {}
        self.daemon = daemon

    def start(self):
        _CapturedThread.started.append(self)

    def is_alive(self):
        return False

    def run(self):
        self.target(**self.kwargs)


@pytest.fixture
def window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    _CapturedThread.started = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    instance = GigaTranscriberQtApp()
    monkeypatch.setattr(threading, "Thread", _CapturedThread)
    instance._lang = "ru"
    processed = []

    class FakePlan:
        def run(self, _callback, *, cancel_check):
            if cancel_check():
                raise processing_mixin.PreparationCancelled("asr")
            return {}

    class FakeProcessor:
        def process_file(self, filepath, output_dir, index, total, **kwargs):
            processed.append(filepath)
            return {
                "file_path": filepath, "file_size": 1, "media_duration": 1,
                "conversion_time": 0, "transcription_time": 0, "total_time": 0,
                "success": True, "saved_files": [],
            }

    monkeypatch.setattr(
        processing_mixin.transcription_service, "build_processing_preparation_plan",
        lambda *_a, **_k: FakePlan(),
    )
    monkeypatch.setattr(
        processing_mixin.transcription_service, "build_processor",
        lambda *_a, **_k: FakeProcessor(),
    )
    monkeypatch.setattr(instance, "_show_completion_dialog", lambda *a, **k: None)
    instance.stats = types.SimpleNamespace(add_processing_record=lambda **_kwargs: None)
    instance.processed = processed
    media = []
    for name in ("first.wav", "second.wav"):
        path = tmp_path / name
        path.write_bytes(b"audio")
        media.append(str(path))
    instance.media = media
    yield instance
    instance.is_processing = False
    instance.close()
    app.processEvents()


def test_clear_all_cancels_the_running_batch_even_after_a_new_start(window):
    window.files_to_process = [window.media[0]]
    window._start_processing_thread()
    first_run = _CapturedThread.started[-1]

    window._clear_all()
    window.files_to_process = [window.media[1]]
    window._start_processing_thread()
    second_run = _CapturedThread.started[-1]
    assert second_run is not first_run
    assert window.is_processing is True

    # Старый воркер доходит до проверки отмены уже после нового «Старт».
    first_run.run()

    assert window.media[0] not in window.processed
    # Его завершение не должно закрыть интерфейс нового запуска.
    assert window.is_processing is True
    assert window.btn_start.isEnabled() is False

    second_run.run()
    QApplication.processEvents()
    assert window.processed == [window.media[1]]
    assert window.is_processing is False


def test_new_start_waits_while_the_cleared_worker_still_runs(window, monkeypatch):
    window.files_to_process = [window.media[0]]
    window._start_processing_thread()
    first_run = _CapturedThread.started[-1]
    monkeypatch.setattr(first_run, "is_alive", lambda: True, raising=False)
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))

    window._clear_all()
    window.files_to_process = [window.media[1]]
    window._start_processing_thread()

    assert _CapturedThread.started == [first_run]
    assert window.is_processing is False
    assert shown and "текущий файл" in shown[0]


def test_cancel_stops_after_current_file_and_reports_cancelled(window):
    window.files_to_process = list(window.media)
    window._start_processing_thread()
    run = _CapturedThread.started[-1]
    window._cancel_processing()

    run.run()
    QApplication.processEvents()

    assert window.processed == []
    assert window.is_processing is False
    assert window.btn_start.isEnabled() is True
