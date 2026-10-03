"""Занятость окна и выход из приложения.

Пакетная обработка, загрузка по ссылке, Live и LLM работают в daemon-потоках:
при выходе процесс их просто убивает. Поэтому окно одной функцией отвечает,
чем оно сейчас занято (_busy_reasons), и по ней же решает, можно ли сменить
модель/движок/устройство и что делать при закрытии. Live-сессию при выходе
не бросают, а останавливают и дожидаются экспорта (с ограничением по времени).

Mixin: методы работают со `self` главного окна.
"""
from __future__ import annotations

import sys
import traceback

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMessageBox

from ..live.types import CaptureState

# Состояния, в которых сессия держит модель, открытые FLAC и потоки
# планировщика: такую сессию нужно остановить, а не бросить. Кортеж, а не
# множество: проверка не должна требовать hash() от чужого объекта состояния.
_LIVE_RUNNING_STATES = (
    CaptureState.STARTING,
    CaptureState.RECORDING,
    CaptureState.PAUSED,
    CaptureState.FAILED,
)
# Сколько ждать экспорта live-сессии при выходе, прежде чем закрыться всё равно.
_LIVE_CLOSE_TIMEOUT_MS = 60_000


def install_exception_hook(window) -> None:
    """Не дать исключению из Qt-слота уронить приложение.

    PyQt6 при стандартном sys.excepthook вызывает qFatal: любое исключение
    в обработчике кнопки или сигнала молча убивает процесс вместе с идущей
    записью. С собственным хуком PyQt только сообщает об исключении, а мы
    пишем traceback в лог приложения и показываем ошибку в журнале.
    """
    previous = sys.excepthook

    def hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            previous(exc_type, exc, tb)
            return
        try:
            window._report_unhandled_exception(exc_type, exc, tb)
        except Exception:  # noqa: BLE001 — хук не должен падать сам
            previous(exc_type, exc, tb)

    sys.excepthook = hook


class LifecycleMixin:
    def _live_activity(self) -> str | None:
        """Что делает Live: 'starting', 'recording', 'finishing' или None."""
        if getattr(self, "_live_starting", False):
            return "starting"
        stop_thread = getattr(self, "_live_stop_thread", None)
        if stop_thread is not None and stop_thread.is_alive():
            return "finishing"
        session = getattr(self, "live_session", None)
        if session is None:
            return None
        try:
            state = session.status().state
        except Exception:
            return None
        if state is CaptureState.STOPPING:
            return "finishing"
        return "recording" if state in _LIVE_RUNNING_STATES else None

    def _busy_items(self, *, model_only: bool = False) -> list[tuple[str, str]]:
        """Занятые подсистемы: (ключ, фраза для пользователя).

        model_only: только то, что держит ASR-модель (пакетная обработка и Live);
        смена модели, движка или устройства выгружает её из-под них.
        """
        items = []
        if self.is_processing or self._processing_worker_alive():
            items.append(("processing", self._t("Идёт обработка файлов.", "File processing is running.")))
        live = self._live_activity()
        if live == "starting":
            items.append(("live", self._t("Запускается live-запись.", "Live capture is starting.")))
        elif live == "recording":
            items.append(("live", self._t("Идёт live-запись.", "Live capture is running.")))
        elif live == "finishing":
            items.append(("live_finishing", self._t("Сохраняется live-сессия.", "The live session is being saved.")))
        if not model_only:
            if self.is_downloading:
                items.append(("download", self._t("Идёт загрузка по ссылке.", "A URL download is running.")))
            if self.is_llm_processing:
                items.append(("llm", self._t("Идёт LLM-обработка.", "LLM processing is running.")))
        return items

    def _busy_reasons(self, *, model_only: bool = False) -> list[str]:
        return [text for _key, text in self._busy_items(model_only=model_only)]

    def _refuse_model_change_while_busy(self, title: str) -> bool:
        """Показать, почему модель/движок/устройство сейчас менять нельзя."""
        reasons = self._busy_reasons(model_only=True)
        if not reasons:
            return False
        QMessageBox.information(
            self,
            title,
            "\n".join(reasons) + "\n\n" + self._t(
                "Дождитесь окончания и повторите.",
                "Wait until it finishes and try again.",
            ),
        )
        return True

    def closeEvent(self, event):
        if not self._close_confirmed:
            # «Сохраняется live-сессия» — это Stop, который пользователь уже
            # нажал: спрашивать не о чем, только дождаться экспорта.
            reasons = [text for key, text in self._busy_items() if key != "live_finishing"]
            if reasons:
                reply = QMessageBox.question(
                    self,
                    self._t("Внимание", "Attention"),
                    "\n".join(reasons) + "\n\n" + self._t(
                        "Закрыть приложение? Обработка, загрузка и LLM-запрос будут прерваны, "
                        "live-запись — остановлена и сохранена.",
                        "Close the application? Processing, download and the LLM request will be "
                        "interrupted; live capture will be stopped and saved.",
                    ),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return
            self._close_confirmed = True
            self._abandon_processing_run()
            self.is_processing = False
        if not self._close_forced and self._live_activity() is not None:
            # Стоп и экспорт идут в фоне; окно закроется по их завершении.
            event.ignore()
            self._close_after_live_session()
            return
        if self.output_dir:
            self.user_settings.set_last_output_dir(self.output_dir)
        if self.input_dir:
            self.user_settings.set_last_files_dir(self.input_dir)
        self._save_ui_settings()
        self._save_geometry()
        self.app_logger.log_session_end()
        event.accept()

    def _close_after_live_session(self) -> None:
        if self._close_pending:
            return
        self._close_pending = True
        if self._live_activity() == "recording":
            self._stop_live_session()
        self._set_status(self._t(
            "Сохраняем live-сессию перед выходом…",
            "Saving the live session before exit…",
        ))
        QTimer.singleShot(_LIVE_CLOSE_TIMEOUT_MS, self._force_close)

    def _continue_pending_close(self) -> None:
        """Live-сессия сохранена (или не смогла): продолжить отложенный выход."""
        if self._close_pending:
            QTimer.singleShot(0, self.close)

    def _force_close(self) -> None:
        if not self._close_pending or self._live_activity() is None:
            return
        self.log(self._t(
            "Live-сессия не успела сохраниться за отведённое время — выходим без неё.",
            "The live session did not finish saving in time — exiting without it.",
        ))
        self._close_forced = True
        self.close()

    def _report_unhandled_exception(self, exc_type, exc, tb) -> None:
        details = "".join(traceback.format_exception(exc_type, exc, tb))
        self.app_logger.get_logger().error("Необработанное исключение:\n%s", details)
        message = self._t(
            f"Внутренняя ошибка: {exc_type.__name__}: {exc}. Подробности — в логе приложения.",
            f"Internal error: {exc_type.__name__}: {exc}. See the application log for details.",
        )
        self.signals.log_message.emit(message)
        self._set_status(message)
