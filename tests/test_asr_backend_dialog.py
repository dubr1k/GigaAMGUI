"""Тесты диалога выбора ASR backend."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from src.gui.asr_backend_dialog import ASRBackendDialog


def test_dialog_disables_mlx_on_unsupported_platform(monkeypatch):
    app = QApplication.instance() or QApplication([])
    dialog = ASRBackendDialog(mlx_supported=False)
    idx = dialog.backend_combo.findData("mlx")
    item = dialog.backend_combo.model().item(idx)
    assert item is not None and not item.isEnabled()


def test_dialog_is_localized_for_russian_parent(monkeypatch):
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    parent._lang = "ru"

    dialog = ASRBackendDialog(parent=parent, mlx_supported=True)
    assert dialog.windowTitle() == "Выбор движка распознавания"
    assert dialog.note_label.text().startswith("Авто:")


def test_dialog_returns_selected_backend(monkeypatch):
    app = QApplication.instance() or QApplication([])
    dialog = ASRBackendDialog(current_backend="pytorch", mlx_supported=True)
    idx = dialog.backend_combo.findData("auto")
    assert idx >= 0
    dialog.backend_combo.setCurrentIndex(idx)
    assert dialog.selected_backend == "auto"
