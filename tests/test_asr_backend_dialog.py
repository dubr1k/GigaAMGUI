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
    assert "ONNX" not in dialog.note_label.text()


def test_dialog_returns_selected_backend(monkeypatch):
    app = QApplication.instance() or QApplication([])
    dialog = ASRBackendDialog(current_backend="pytorch", mlx_supported=True)
    idx = dialog.backend_combo.findData("auto")
    assert idx >= 0
    dialog.backend_combo.setCurrentIndex(idx)
    assert dialog.selected_backend == "auto"


def test_dialog_exposes_onnx_backend_and_independent_provider():
    app = QApplication.instance() or QApplication([])
    dialog = ASRBackendDialog(
        current_backend="onnx",
        current_provider="cuda",
        mlx_supported=False,
    )

    assert dialog.backend_combo.findData("onnx") >= 0
    assert dialog.selected_backend == "onnx"
    assert dialog.selected_provider == "cuda"


def test_dialog_lists_come_from_runtime_options_in_the_same_order():
    from src.core.runtime_options import ASR_BACKENDS, ONNX_PROVIDERS

    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    parent._lang = "en"
    dialog = ASRBackendDialog(parent=parent, mlx_supported=True)

    combo = dialog.provider_combo
    assert [combo.itemData(i) for i in range(combo.count())] == list(ONNX_PROVIDERS)
    assert [combo.itemText(i) for i in range(combo.count())] == [
        "Auto", "CPU", "CUDA", "TENSORRT", "COREML", "DIRECTML",
    ]
    backends = dialog.backend_combo
    assert [backends.itemData(i) for i in range(backends.count())] == list(ASR_BACKENDS)
    assert [backends.itemText(i) for i in range(backends.count())] == ["Auto", "MLX", "ONNX Runtime", "PyTorch"]
