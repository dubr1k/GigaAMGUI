"""Диалог выбора backend-а ASR."""

from __future__ import annotations

import importlib
import platform
import sys

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)


def _module_available(module_name: str) -> bool:
    try:
        importlib.import_module(module_name)
        return True
    except Exception:
        return False


def is_mlx_supported() -> bool:
    if sys.platform != "darwin" or platform.machine() != "arm64":
        return False
    return _module_available("mlx") and _module_available("gigaam_mlx")


class ASRBackendDialog(QDialog):
    """Выбор backend-а ASR для одного процесса приложения."""

    LABELS = {
        "auto": "Auto",
        "mlx": "MLX",
        "pytorch": "PyTorch",
    }

    NOTES = {
        "auto": "Авто: MLX (Apple Silicon) -> PyTorch fallback",
        "mlx": "MLX: доступен только на macOS Apple Silicon",
        "pytorch": "PyTorch: кросс-платформенный backend",
    }

    def __init__(self, parent=None, *, current_backend: str = "auto", mlx_supported: bool | None = None):
        super().__init__(parent)
        self.setWindowTitle("Выбор backend распознавания")

        self._mlx_supported = bool(is_mlx_supported() if mlx_supported is None else mlx_supported)
        self._selected_backend = "auto"

        self.backend_combo = QComboBox(self)
        self.backend_combo.setObjectName("asr_backend_combo")
        for backend in ("auto", "mlx", "pytorch"):
            self.backend_combo.addItem(self.LABELS[backend], backend)

        mlx_index = self.backend_combo.findData("mlx")
        if mlx_index >= 0 and not self._mlx_supported:
            item = self.backend_combo.model().item(mlx_index)
            if item is not None:
                item.setEnabled(False)

        selected = self.backend_combo.findData(current_backend)
        self.backend_combo.setCurrentIndex(selected if selected >= 0 else 0)

        self.note_label = QLabel(self.NOTES[self.backend_combo.currentData()])
        self.backend_combo.currentTextChanged.connect(self._on_selection_changed)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        is_ru = getattr(parent, "_lang", "ru") == "ru"
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("ОК" if is_ru else "OK")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена" if is_ru else "Cancel")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Backend ASR:"))
        row.addWidget(self.backend_combo)
        layout.addLayout(row)
        layout.addWidget(self.note_label)
        layout.addWidget(buttons)

    def _on_selection_changed(self, _text: str) -> None:
        data = self.backend_combo.currentData()
        key = data if data in self.NOTES else "auto"
        self.note_label.setText(self.NOTES[key])

    @property
    def selected_backend(self) -> str:
        return self.backend_combo.currentData()

    @classmethod
    def pick(cls, parent=None, *, current_backend: str = "auto", mlx_supported: bool | None = None) -> str | None:
        dialog = cls(parent, current_backend=current_backend, mlx_supported=mlx_supported)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.selected_backend
