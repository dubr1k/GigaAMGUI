"""
Диалоги выбора вычислительного устройства (CPU / GPU / GPU 50xx) и загрузки
соответствующей сборки PyTorch.

Модуль использует только PyQt6 и runtime_manager — torch здесь не импортируется,
поэтому диалог можно показывать на самом раннем этапе старта, ДО загрузки torch.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from ..utils import runtime_manager as rm


class _InstallWorker(QThread):
    """Устанавливает выбранный вариант torch в фоновом потоке."""

    log = pyqtSignal(str)
    done = pyqtSignal(bool)

    def __init__(self, variant: str):
        super().__init__()
        self._variant = variant

    def run(self):
        try:
            ok = rm.install_variant(self._variant, log_cb=self.log.emit)
        except Exception as e:  # noqa: BLE001 — показываем пользователю любую ошибку
            self.log.emit(f"Непредвиденная ошибка: {e}")
            ok = False
        self.done.emit(ok)


class DeviceSelectDialog(QDialog):
    """Окно выбора устройства с радиокнопками вариантов."""

    def __init__(self, parent=None, recommended: str | None = None,
                 current: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Выбор вычислительного устройства")
        self.setMinimumWidth(560)
        self._selected: str | None = None

        recommended = recommended or rm.detect_recommended_variant()

        layout = QVBoxLayout(self)
        title = QLabel("На чём выполнять распознавание речи?")
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(title)

        subtitle = QLabel(
            "Выберите один раз. Нужная сборка PyTorch скачается автоматически и "
            "сохранится — при повторном выборе она уже не будет загружаться заново."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #888; margin-bottom: 6px;")
        layout.addWidget(subtitle)

        self._group = QButtonGroup(self)
        self._buttons: dict[str, QRadioButton] = {}

        for variant, info in rm.VARIANTS.items():
            installed = rm.is_installed(variant)
            tags = []
            if variant == recommended:
                tags.append("рекомендуется для вашего ПК")
            tags.append("уже загружено" if installed else f"загрузка {info['size_hint']}")
            tag_text = " · ".join(tags)

            rb = QRadioButton(f"{info['label']}")
            rb.setStyleSheet("font-size: 13px; font-weight: 600;")
            self._group.addButton(rb)
            self._buttons[variant] = rb
            layout.addWidget(rb)

            hint = QLabel(f"    {info['hint']}\n    ({tag_text})")
            hint.setWordWrap(True)
            hint.setStyleSheet("color: #999; font-size: 11px; margin-bottom: 6px;")
            layout.addWidget(hint)

            if variant == (current or recommended):
                rb.setChecked(True)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_cancel = QPushButton("Отмена")
        self._btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self._btn_cancel)
        self._btn_ok = QPushButton("Продолжить")
        self._btn_ok.setDefault(True)
        self._btn_ok.clicked.connect(self._accept)
        btn_row.addWidget(self._btn_ok)
        layout.addLayout(btn_row)

    def _accept(self):
        for variant, rb in self._buttons.items():
            if rb.isChecked():
                self._selected = variant
                self.accept()
                return
        QMessageBox.warning(self, "Выбор устройства", "Выберите вариант.")

    def selected_variant(self) -> str | None:
        return self._selected


class InstallProgressDialog(QDialog):
    """Окно загрузки/установки выбранной сборки torch с логом."""

    def __init__(self, variant: str, parent=None):
        super().__init__(parent)
        self._variant = variant
        self._success = False
        self.setWindowTitle("Загрузка PyTorch")
        self.setMinimumWidth(620)
        self.setModal(True)

        layout = QVBoxLayout(self)
        self._label = QLabel(
            f"Устанавливается: {rm.VARIANTS[variant]['label']}\n"
            "Не закрывайте окно — идёт загрузка."
        )
        self._label.setWordWrap(True)
        layout.addWidget(self._label)

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)  # бесконечный индикатор
        layout.addWidget(self._bar)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMinimumHeight(240)
        self._log.setStyleSheet("font-family: Consolas, monospace; font-size: 11px;")
        layout.addWidget(self._log)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_close = QPushButton("Закрыть")
        self._btn_close.setEnabled(False)
        self._btn_close.clicked.connect(self.accept)
        btn_row.addWidget(self._btn_close)
        layout.addLayout(btn_row)

        self._worker = _InstallWorker(variant)
        self._worker.log.connect(self._on_log)
        self._worker.done.connect(self._on_done)

    def start(self):
        self._worker.start()

    def _on_log(self, line: str):
        if line:
            self._log.appendPlainText(line)

    def _on_done(self, ok: bool):
        self._success = ok
        self._bar.setRange(0, 1)
        self._bar.setValue(1)
        if ok:
            self._label.setText("Готово! PyTorch установлен.")
        else:
            self._label.setText("Не удалось установить PyTorch. См. лог ниже.")
        self._btn_close.setEnabled(True)

    def succeeded(self) -> bool:
        return self._success

    def closeEvent(self, event):
        # Не даём закрыть окно, пока идёт установка.
        if self._worker.isRunning():
            event.ignore()
        else:
            super().closeEvent(event)


def _install_with_progress(variant: str, parent=None) -> bool:
    """Показывает окно загрузки и ждёт завершения. Возвращает успех."""
    dlg = InstallProgressDialog(variant, parent)
    dlg.start()
    dlg.exec()
    return dlg.succeeded()


def ensure_device_ready(parent=None) -> str | None:
    """
    Гарантирует, что выбран и установлен рабочий вариант torch.

    Вызывается при старте до импорта torch. Если устройство уже выбрано и
    установлено — сразу возвращает его без диалога. Иначе показывает выбор и,
    при необходимости, скачивает сборку.

    Возвращает имя варианта ('cpu'/'cu124'/'cu128') либо None, если пользователь
    отменил и запускать нечего.
    """
    rm.ensure_data_dir()

    variant = rm.get_selected_variant()
    if variant and rm.is_installed(variant):
        return variant

    # Если вариант единственный (macOS) — не показываем выбор, сразу ставим.
    if len(rm.VARIANTS) == 1:
        only = next(iter(rm.VARIANTS))
        if rm.is_installed(only) or _install_with_progress(only, parent):
            rm.set_selected_variant(only)
            return only
        return None

    # Нужен выбор.
    while True:
        dlg = DeviceSelectDialog(parent, current=variant)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        chosen = dlg.selected_variant()
        if not chosen:
            return None

        if rm.is_installed(chosen):
            rm.set_selected_variant(chosen)
            return chosen

        if _install_with_progress(chosen, parent):
            rm.set_selected_variant(chosen)
            return chosen

        # Установка не удалась — предложим выбрать снова.
        retry = QMessageBox.question(
            parent, "Ошибка загрузки",
            "Не удалось загрузить PyTorch. Попробовать другой вариант?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if retry != QMessageBox.StandardButton.Yes:
            return None


def change_device_interactive(parent=None) -> bool:
    """
    Открывает выбор устройства из настроек (пункт меню).

    Позволяет сменить вариант и, при необходимости, догрузить его. Уже
    установленные варианты не перекачиваются. Возвращает True, если выбор
    изменён и требуется перезапуск приложения.
    """
    current = rm.get_selected_variant()
    dlg = DeviceSelectDialog(parent, current=current)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    chosen = dlg.selected_variant()
    if not chosen:
        return False

    if not rm.is_installed(chosen):
        if not _install_with_progress(chosen, parent):
            QMessageBox.warning(parent, "Ошибка", "Не удалось загрузить выбранную сборку.")
            return False

    if chosen == current:
        return False

    rm.set_selected_variant(chosen)
    QMessageBox.information(
        parent, "Устройство изменено",
        f"Выбрано: {rm.VARIANTS[chosen]['label']}.\n"
        "Изменение вступит в силу после перезапуска приложения.",
    )
    return True
