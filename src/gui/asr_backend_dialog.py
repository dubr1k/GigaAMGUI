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
        "auto": ("Авто", "Auto"),
        "mlx": ("MLX", "MLX"),
        "onnx": ("ONNX Runtime", "ONNX Runtime"),
        "pytorch": ("PyTorch", "PyTorch"),
    }

    NOTES = {
        "auto": (
            "Авто: MLX на Apple Silicon, иначе PyTorch",
            "Auto: MLX on Apple Silicon, otherwise PyTorch",
        ),
        "mlx": (
            "MLX: доступен только на macOS Apple Silicon",
            "MLX: available only on macOS Apple Silicon",
        ),
        "onnx": (
            "ONNX Runtime: лёгкий кросс-платформенный backend",
            "ONNX Runtime: lightweight cross-platform backend",
        ),
        "pytorch": (
            "PyTorch: кросс-платформенный backend",
            "PyTorch: cross-platform backend",
        ),
    }

    PROVIDERS = ("auto", "cpu", "cuda", "tensorrt", "coreml", "directml")

    def __init__(
        self,
        parent=None,
        *,
        current_backend: str = "auto",
        current_provider: str = "auto",
        mlx_supported: bool | None = None,
    ):
        super().__init__(parent)
        self._is_ru = getattr(parent, "_lang", "ru") == "ru"
        self.setWindowTitle("Выбор движка распознавания" if self._is_ru else "Recognition engine")

        self._mlx_supported = bool(is_mlx_supported() if mlx_supported is None else mlx_supported)
        self._selected_backend = "auto"

        self.backend_combo = QComboBox(self)
        self.backend_combo.setObjectName("asr_backend_combo")
        for backend in ("auto", "mlx", "onnx", "pytorch"):
            self.backend_combo.addItem(self._label(backend), backend)

        mlx_index = self.backend_combo.findData("mlx")
        if mlx_index >= 0 and not self._mlx_supported:
            item = self.backend_combo.model().item(mlx_index)
            if item is not None:
                item.setEnabled(False)

        selected = self.backend_combo.findData(current_backend)
        self.backend_combo.setCurrentIndex(selected if selected >= 0 else 0)

        self.note_label = QLabel(self._note(self.backend_combo.currentData()))
        self.backend_combo.currentTextChanged.connect(self._on_selection_changed)

        self.provider_combo = QComboBox(self)
        self.provider_combo.setObjectName("onnx_provider_combo")
        for provider in self.PROVIDERS:
            self.provider_combo.addItem(provider.upper() if provider != "auto" else self._label("auto"), provider)
        provider_index = self.provider_combo.findData(current_provider)
        self.provider_combo.setCurrentIndex(provider_index if provider_index >= 0 else 0)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("ОК" if self._is_ru else "OK")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена" if self._is_ru else "Cancel")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Движок распознавания:" if self._is_ru else "Recognition engine:"))
        row.addWidget(self.backend_combo)
        layout.addLayout(row)
        provider_row = QHBoxLayout()
        provider_row.addWidget(QLabel("ONNX provider:"))
        provider_row.addWidget(self.provider_combo)
        layout.addLayout(provider_row)
        layout.addWidget(self.note_label)
        layout.addWidget(buttons)

    def _label(self, key: str) -> str:
        ru, en = self.LABELS.get(key, (key, key))
        return ru if self._is_ru else en

    def _note(self, key: str) -> str:
        ru, en = self.NOTES.get(key, self.NOTES["auto"])
        return ru if self._is_ru else en

    def _on_selection_changed(self, _text: str) -> None:
        data = self.backend_combo.currentData()
        key = data if data in self.NOTES else "auto"
        self.note_label.setText(self._note(key))

    @property
    def selected_backend(self) -> str:
        return self.backend_combo.currentData()

    @property
    def selected_provider(self) -> str:
        return self.provider_combo.currentData()

    @classmethod
    def pick(cls, parent=None, *, current_backend: str = "auto", mlx_supported: bool | None = None) -> str | None:
        dialog = cls(parent, current_backend=current_backend, mlx_supported=mlx_supported)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.selected_backend

    @classmethod
    def pick_configuration(
        cls,
        parent=None,
        *,
        current_backend: str = "auto",
        current_provider: str = "auto",
        mlx_supported: bool | None = None,
    ) -> tuple[str, str] | None:
        dialog = cls(
            parent,
            current_backend=current_backend,
            current_provider=current_provider,
            mlx_supported=mlx_supported,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.selected_backend, dialog.selected_provider
