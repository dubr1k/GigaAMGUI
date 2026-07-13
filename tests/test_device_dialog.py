import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.gui.device_dialog import InstallProgressDialog
from src.utils import runtime_manager as rm


class _FakeWorker:
    def __init__(self):
        self.cancel_called = False

    def isRunning(self):
        return True

    def cancel_requested(self):
        return False

    def cancel(self):
        self.cancel_called = True


def test_install_progress_dialog_cancel_button_requests_cancellation():
    app = QApplication.instance() or QApplication([])
    dialog = InstallProgressDialog(next(iter(rm.VARIANTS)))
    fake_worker = _FakeWorker()
    dialog._worker = fake_worker

    dialog._request_cancel()

    assert dialog.cancelled() is True
    assert fake_worker.cancel_called is True
    assert dialog._btn_cancel.isEnabled() is False
