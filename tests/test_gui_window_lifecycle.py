"""Занятость окна: выход и смена модели/движка/устройства во время работы.

Обработка, Live и LLM работают в daemon-потоках. Выход без вопроса убивал
их вместе с процессом: live-сессия теряла экспорт и недописанные FLAC,
LLM-результат пропадал молча. Смена модели проверяла только пакетную
обработку и выгружала модель из-под идущей Live-записи.
"""

import os
import sys
import threading
import time
import types
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402

sys.modules.setdefault("gigaam", types.SimpleNamespace(load_model=lambda *args, **kwargs: object()))
sys.modules.setdefault("yt_dlp", types.SimpleNamespace(YoutubeDL=object))

from src.gui.app_qt import GigaTranscriberQtApp  # noqa: E402
from src.gui.asr_backend_dialog import ASRBackendDialog  # noqa: E402
from src.live.types import CaptureState  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_gui_config(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGAAM_CONFIG_DIR", str(tmp_path / "config"))


class FakeSession:
    def __init__(self, tmp_path: Path, *, state=CaptureState.RECORDING, stop_error=None, release=None):
        self.state = state
        self.stop_error = stop_error
        self.release = release
        self.stop_calls = 0
        self.session_dir = tmp_path / "2026-10-03_12-00-00"
        self.session_dir.mkdir(exist_ok=True)

    def status(self):
        return types.SimpleNamespace(state=self.state)

    def stop(self):
        self.stop_calls += 1
        self.state = CaptureState.STOPPING
        if self.release is not None:
            self.release.wait(10)
        if self.stop_error is not None:
            raise self.stop_error
        self.state = CaptureState.STOPPED
        return types.SimpleNamespace(session_dir=self.session_dir)

    def conversation(self):
        return []


def _pump_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    QApplication.processEvents()
    return predicate()


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    instance = GigaTranscriberQtApp()
    instance._lang = "ru"
    instance.show()
    app.processEvents()
    yield instance
    instance.is_processing = False
    instance.is_llm_processing = False
    instance.is_downloading = False
    instance.live_session = None
    instance.close()
    app.processEvents()


def test_close_stops_a_running_live_session_and_waits_for_its_export(window, tmp_path, monkeypatch):
    questions = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **_kwargs: questions.append(args[2]) or QMessageBox.StandardButton.Yes,
    )
    session = FakeSession(tmp_path)
    window.live_session = session

    window.close()

    assert questions and "live" in questions[0].lower()
    assert _pump_until(lambda: not window.isVisible())
    assert session.stop_calls == 1


def test_close_keeps_the_window_when_the_live_question_is_declined(window, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    session = FakeSession(tmp_path)
    window.live_session = session

    window.close()
    QApplication.processEvents()

    assert window.isVisible()
    assert session.stop_calls == 0


def test_close_asks_while_an_llm_request_runs(window, monkeypatch):
    questions = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **_kwargs: questions.append(args[2]) or QMessageBox.StandardButton.No,
    )
    window.is_llm_processing = True

    window.close()

    assert questions and "LLM" in questions[0]
    assert window.isVisible()


def test_close_during_a_requested_stop_waits_without_asking(window, tmp_path, monkeypatch):
    def unexpected_question(*_args, **_kwargs):
        raise AssertionError("the user already pressed Stop")

    monkeypatch.setattr(QMessageBox, "question", unexpected_question)
    release = threading.Event()
    session = FakeSession(tmp_path, release=release)
    window.live_session = session
    window._stop_live_session()

    window.close()
    QApplication.processEvents()
    assert window.isVisible(), "closing mid-export would kill the daemon stop thread"

    release.set()
    assert _pump_until(lambda: not window.isVisible())


def test_close_gives_up_waiting_for_a_hung_live_export(window, tmp_path, monkeypatch):
    from src.gui import lifecycle_mixin

    monkeypatch.setattr(lifecycle_mixin, "_LIVE_CLOSE_TIMEOUT_MS", 200)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    release = threading.Event()
    window.live_session = FakeSession(tmp_path, release=release)

    window.close()

    try:
        assert _pump_until(lambda: not window.isVisible(), timeout=3)
    finally:
        release.set()


def test_failed_live_stop_reenables_the_controls_and_shows_why(window, tmp_path):
    window.live_session = FakeSession(tmp_path, stop_error=RuntimeError("disk full"))
    window._update_live_control_state(CaptureState.RECORDING)
    assert window.btn_live_start.isEnabled() is False

    window._stop_live_session()
    assert _pump_until(lambda: "disk full" in window.lbl_live_problem.text())

    assert window.btn_live_start.isEnabled() is True
    assert window.btn_live_stop.isEnabled() is False


@pytest.mark.parametrize("busy", ["live", "processing"])
def test_model_backend_and_device_changes_wait_for_live_and_processing(window, tmp_path, monkeypatch, busy):
    if busy == "live":
        window.live_session = FakeSession(tmp_path)
    else:
        window.is_processing = True
    opened = []
    shown = []
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: opened.append("model") or ("", False))
    monkeypatch.setattr(ASRBackendDialog, "pick_configuration", lambda *a, **k: opened.append("backend"))
    monkeypatch.setattr(
        "src.gui.device_dialog.change_device_interactive", lambda *a, **k: opened.append("device"),
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))

    window._select_asr_model()
    window._select_asr_backend()
    window._change_device()

    assert opened == []
    assert len(shown) == 3
    expected = "live" if busy == "live" else "обработка"
    assert all(expected in message.lower() for message in shown)


def test_journal_table_is_rebuilt_only_when_a_log_line_changes_it(window, monkeypatch):
    rebuilds = []
    original = window._filter_journal_rows
    monkeypatch.setattr(window, "_filter_journal_rows", lambda *a: rebuilds.append(1) or original())

    for number in range(50):
        window._append_log(f"Распознавание: чанк {number}")
    assert rebuilds == []

    window._append_log("--- Обработка файла 1/1: talk.wav ---")
    window._append_log("Длительность: 00:01:00")
    window._append_log("Время обработки: 12 с")
    assert len(rebuilds) == 3
    assert window._journal_entries[-1] == {
        "file": "talk.wav", "duration": "00:01:00", "status": "ready",
        "date": window._journal_entries[-1]["date"],
    }
    assert window.journal_table.rowCount() == 1
