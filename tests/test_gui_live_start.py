"""Запуск Live не блокирует окно, а ошибки сессии не роняют приложение.

Загрузка и прогрев модели занимали Qt-поток на секунды (минуты при первом
скачивании), а processEvents() внутри пропускал второй клик. Исключение
из Qt-слота в PyQt6 — это qFatal: вопрос ассистенту после Stop, ошибка
создания папки сессии, сбой pause()/resume() убивали процесс целиком.
"""

import os
import sys
import threading
import time
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

sys.modules.setdefault("gigaam", types.SimpleNamespace(load_model=lambda *args, **kwargs: object()))
sys.modules.setdefault("yt_dlp", types.SimpleNamespace(YoutubeDL=object))

from src.gui.app_qt import GigaTranscriberQtApp  # noqa: E402
from src.live.types import CaptureSource, CaptureState, TranscriptEvent  # noqa: E402


class _Scheduler:
    def __init__(self, backend, *, on_final, on_partial, on_error):
        self.backend = backend

    def submit(self, chunk):
        pass

    def flush(self):
        pass

    def close(self):
        pass


class _SlowLoader:
    """Модель, которая грузится, пока тест не разрешит."""

    requested_backend = "auto"
    requested_provider = "auto"
    requested_model = "v3_e2e_rnnt"

    def __init__(self):
        self.release = threading.Event()
        self.loads = 0
        self.loaded = False

    def is_loaded(self):
        return self.loaded

    def load_model(self, logger=None):
        self.loads += 1
        self.release.wait(3)
        self.loaded = True
        return True

    def transcribe_window(self, audio, sample_rate, offset_samples):
        return []

    def diagnostics(self):
        return {}


class _ReadyLoader(_SlowLoader):
    def __init__(self):
        super().__init__()
        self.loaded = True


@pytest.fixture(autouse=True)
def _isolated_live(monkeypatch, tmp_path):
    from src.live.capture.noop import NoOpCaptureAdapter

    monkeypatch.setenv("GIGAAM_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr("src.gui.live_mixin.LiveAsrScheduler", _Scheduler)
    monkeypatch.setattr(
        "src.gui.live_mixin.create_capture_adapter",
        lambda _platform, source, device_id=None: NoOpCaptureAdapter(source, device_id),
    )


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    instance = GigaTranscriberQtApp()
    instance._lang = "ru"
    instance.live_output_dir.setText(str(tmp_path / "sessions"))
    (tmp_path / "sessions").mkdir()
    instance.show()
    yield instance
    session = instance.live_session
    if session is not None and session.status().state in (
        CaptureState.RECORDING, CaptureState.PAUSED, CaptureState.FAILED,
    ):
        session.stop()
    instance.live_session = None
    instance._live_starting = False
    instance.close()
    app.processEvents()


def _pump_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    QApplication.processEvents()
    return predicate()


def test_live_start_loads_the_model_off_the_qt_thread_and_ignores_a_second_click(window):
    loader = _SlowLoader()
    window.model_loader = loader

    started = time.monotonic()
    window._start_live_session()
    window._start_live_session()
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, "model load blocked the Qt thread"
    assert window.btn_live_start.isEnabled() is False
    assert window.btn_live_stop.isEnabled() is False
    assert "Загрузка модели" in window.lbl_live_status.text()

    loader.release.set()
    assert _pump_until(lambda: window.live_session is not None)
    assert loader.loads == 1
    assert window.live_session.status().state is CaptureState.RECORDING
    assert window.btn_live_stop.isEnabled() is True


def test_closing_while_the_model_loads_does_not_start_a_session(window, monkeypatch):
    loader = _SlowLoader()
    window.model_loader = loader
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window._start_live_session()

    window.close()
    QApplication.processEvents()
    assert window.isVisible()

    loader.release.set()
    assert _pump_until(lambda: not window.isVisible())
    assert window.live_session is None


def test_question_after_stop_is_answered_with_an_error_not_an_abort(window, monkeypatch):
    window.model_loader = _ReadyLoader()
    window._start_live_session()
    assert _pump_until(lambda: window.live_session is not None)
    window.live_session._on_final(TranscriptEvent(
        "event-1", 0, CaptureSource.MIC, 0, 16_000, 1_000_000_000, "Final line", "final",
    ))
    window._stop_live_session()
    assert _pump_until(lambda: not window._live_stop_thread.is_alive())
    monkeypatch.setattr(window, "_collect_llm_settings", lambda: {"provider": "API"})
    window._show_live_overlay()

    window._answer_live_question("Что обсуждали?")

    assert "остановлена" in window.live_overlay.answer_text.toPlainText()
    assert window.live_overlay.send_button.isEnabled() is True


def test_session_folder_failure_is_reported_and_start_stays_available(window, monkeypatch):
    window.model_loader = _ReadyLoader()

    def broken_session(*_args, **_kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr("src.gui.live_mixin.LiveSession", broken_session)

    window._start_live_session()
    assert _pump_until(lambda: not window._live_starting)

    assert window.live_session is None
    assert "read-only file system" in window.lbl_live_problem.text()
    assert window.btn_live_start.isEnabled() is True


def test_pause_and_resume_failures_are_reported(window):
    class Session:
        def __init__(self, state, error):
            self.state = state
            self.error = error

        def status(self):
            return types.SimpleNamespace(state=self.state)

        def pause(self):
            raise self.error

        def resume(self):
            raise self.error

    window.live_session = Session(CaptureState.RECORDING, OSError("PortAudio stream error"))
    window._pause_live_session()
    assert "PortAudio stream error" in window.lbl_live_problem.text()

    window.live_session = Session(CaptureState.PAUSED, OSError("ScreenCaptureKit restart failed"))
    window._start_live_session()
    assert "ScreenCaptureKit restart failed" in window.lbl_live_problem.text()
    window.live_session = None


def test_exception_hook_logs_slot_errors_instead_of_aborting(window, monkeypatch):
    from src.gui.lifecycle_mixin import install_exception_hook

    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    install_exception_hook(window)
    try:
        raise ValueError("broken slot")
    except ValueError:
        sys.excepthook(*sys.exc_info())

    assert "broken slot" in window.log_text.toPlainText()
    assert "Внутренняя ошибка" in window.statusBar().currentMessage()

