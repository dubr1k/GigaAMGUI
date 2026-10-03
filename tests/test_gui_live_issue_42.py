"""Regression cover for issue #42: silent live sessions and a stuck overlay."""

import os
import sys
import time
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QKeyEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

sys.modules.setdefault("gigaam", types.SimpleNamespace(load_model=lambda *args, **kwargs: object()))
sys.modules.setdefault("yt_dlp", types.SimpleNamespace(YoutubeDL=object))

from src.gui.app_qt import GigaTranscriberQtApp  # noqa: E402
from src.gui.live_overlay import LiveOverlay  # noqa: E402
from src.live.types import CaptureEvent, CaptureEventKind, CaptureSource  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_gui_config(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGAAM_CONFIG_DIR", str(tmp_path / "config"))

    class Scheduler:
        def __init__(self, backend, *, on_final, on_partial, on_error):
            self.backend = backend

        def submit(self, chunk):
            pass

        def flush(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("src.gui.live_mixin.LiveAsrScheduler", Scheduler)


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    instance = GigaTranscriberQtApp()
    yield instance
    if instance.live_session is not None and instance.live_session.status().state.value in {
        "recording", "paused", "failed",
    }:
        instance.live_session.stop()


def _wait_for_live_start(window, timeout=30.0):
    """Model load and warm-up run off the Qt thread; wait for the session."""
    deadline = time.monotonic() + timeout
    while window._live_starting and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    QApplication.processEvents()
    assert not window._live_starting, "live session did not finish starting"


def _start_live(window, tmp_path, monkeypatch):
    from src.live.capture.noop import NoOpCaptureAdapter

    monkeypatch.setattr(
        "src.gui.live_mixin.create_capture_adapter",
        lambda platform, source, device_id=None: NoOpCaptureAdapter(source, device_id),
    )
    monkeypatch.setattr(window, "_preload_live_model", lambda: True)
    window.live_output_dir.setText(str(tmp_path))
    window._start_live_session()
    _wait_for_live_start(window)


# --- overlay -----------------------------------------------------------------

def test_overlay_button_toggles_visibility(window):
    window.btn_live_overlay.setChecked(True)
    window._toggle_live_overlay()
    assert window.live_overlay.isVisible() is True

    window.btn_live_overlay.setChecked(False)
    window._toggle_live_overlay()
    assert window.live_overlay.isVisible() is False


def test_hiding_overlay_from_its_own_close_button_unchecks_the_toggle(window):
    window.btn_live_overlay.setChecked(True)
    window._toggle_live_overlay()

    window.live_overlay.close_button.click()

    assert window.live_overlay.isVisible() is False
    assert window.btn_live_overlay.isChecked() is False


def test_overlay_escape_hides_without_destroying(qapp):
    overlay = LiveOverlay()
    overlay.show()
    seen = []
    overlay.visibility_changed.connect(seen.append)

    overlay.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))

    assert overlay.isVisible() is False
    assert seen[-1] is False
    overlay.close()


def test_overlay_visibility_is_restored_from_settings(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("GIGAAM_CONFIG_DIR", str(tmp_path / "config"))
    first = GigaTranscriberQtApp()
    first.btn_live_overlay.setChecked(True)
    first._toggle_live_overlay()
    first._save_live_settings()

    second = GigaTranscriberQtApp()
    try:
        assert second._live_settings["overlay_visible"] is True
        assert second.btn_live_overlay.isChecked() is True
    finally:
        second.close()
        first.close()


# --- diagnostics -------------------------------------------------------------

def test_live_capture_events_reach_the_processing_log(window, tmp_path, monkeypatch):
    _start_live(window, tmp_path, monkeypatch)
    logged = []
    window.signals.log_message.connect(logged.append)

    window.live_session._on_event(
        CaptureEvent(CaptureEventKind.OVERFLOW, CaptureSource.MIC, 0, 1, "queue full; dropped_frames=480")
    )
    QApplication.processEvents()

    assert any("queue full" in message for message in logged)
    assert "queue full" in window.log_text.toPlainText()


def test_live_problem_banner_persists_after_status_updates(window, tmp_path, monkeypatch):
    _start_live(window, tmp_path, monkeypatch)

    window._update_live_event(
        CaptureEvent(CaptureEventKind.STATUS, CaptureSource.MIC, 0, 1, "Mixed audio recording disabled")
    )
    window._update_live_status(window.live_session.status())

    assert window.lbl_live_problem.isHidden() is False
    assert "Mixed audio recording disabled" in window.lbl_live_problem.text()


def test_starting_a_session_clears_the_previous_problem_banner(window, tmp_path, monkeypatch):
    window.lbl_live_problem.setText("old failure")
    window.lbl_live_problem.show()

    _start_live(window, tmp_path, monkeypatch)

    assert window.lbl_live_problem.text() == ""
    assert window.lbl_live_problem.isHidden() is True


def test_live_session_receives_a_log_sink(window, tmp_path, monkeypatch):
    _start_live(window, tmp_path, monkeypatch)

    assert (window.live_session.session_dir / "live.log").exists()


def test_failed_model_preload_reports_instead_of_starting_silently(window, tmp_path, monkeypatch):
    from src.live.capture.noop import NoOpCaptureAdapter

    monkeypatch.setattr(
        "src.gui.live_mixin.create_capture_adapter",
        lambda platform, source, device_id=None: NoOpCaptureAdapter(source, device_id),
    )

    class BrokenLoader:
        def is_loaded(self):
            return False

        def load_model(self, logger=None):
            return False

        def diagnostics(self):
            return {"error": "model files are missing"}

    monkeypatch.setattr(window, "model_loader", BrokenLoader())
    window.live_output_dir.setText(str(tmp_path))
    window._start_live_session()
    _wait_for_live_start(window)

    assert window.live_session is None
    assert "model files are missing" in window.lbl_live_problem.text()
