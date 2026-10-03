"""Live-tab settings and Qt-facing capture session controller."""

from __future__ import annotations

import re
import sys
import threading
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QTextCursor
from PyQt6.QtWidgets import QFileDialog

from ..live.asr import LiveAsrScheduler
from ..live.asr_backend import LazyModelBackend
from ..live.capture.factory import CaptureUnavailable, create_capture_adapter
from ..live.exports import ExportSelection
from ..live.journal import default_session_root
from ..live.session import LiveSession, LiveStatus
from ..live.types import (
    CaptureEvent,
    CaptureEventKind,
    CaptureSource,
    CaptureState,
    DiarizationMode,
    LiveSettings,
    TranscriptEvent,
)
from .live_overlay import LiveOverlay
from .live_transcript import LiveTranscriptPresenter


def _display_path(path: str | Path) -> str:
    """Abbreviate the home folder the way Finder and the Liquid client do."""
    path = Path(path)
    try:
        return str(Path("~") / path.relative_to(Path.home()))
    except ValueError:
        return str(path)


class LiveMixin:
    def _init_live_state(self) -> None:
        self.live_session = None
        self.live_overlay = None
        self._live_settings = self.user_settings.get_value("live_settings", {}) or {}
        self._live_transcript_presenter = LiveTranscriptPresenter()
        self._live_capture_status_times: dict[tuple[CaptureSource, str], float] = {}
        self._live_llm_cancel_event: threading.Event | None = None
        self._live_conversation_id: str | None = None
        self._live_stop_thread: threading.Thread | None = None
        self._live_starting = False
        # Перечисление устройств идёт в фоне; до ответа выбор хранится здесь.
        self._live_devices_probing = False
        self._live_devices_generation = 0
        self._live_device_selection: dict[CaptureSource, str | None] = {}

    def _restore_live_settings(self) -> None:
        settings = self._live_settings
        self._set_live_combo_value(self.combo_live_source, settings.get("source", "mic"))
        self.cb_live_mic_audio.setChecked(bool(settings.get("record_mic_audio", True)))
        self.cb_live_system_audio.setChecked(bool(settings.get("record_system_audio", True)))
        self._set_live_combo_value(self.combo_live_diarization, settings.get("diarization_mode", "off"))
        self.cb_live_export_txt.setChecked(bool(settings.get("export_txt", True)))
        self.cb_live_export_txt_timecodes.setChecked(bool(settings.get("export_txt_timecodes", True)))
        self.cb_live_export_txt_diarize.setChecked(bool(settings.get("export_txt_diarize", False)))
        self.cb_live_export_txt_diarize_timecodes.setChecked(bool(settings.get("export_txt_diarize_timecodes", False)))
        self.cb_live_export_md.setChecked(bool(settings.get("export_md", False)))
        self.cb_live_export_srt.setChecked(bool(settings.get("export_srt", False)))
        self.cb_live_export_vtt.setChecked(bool(settings.get("export_vtt", False)))
        self.cb_live_subtitle_sentence_split.setChecked(bool(settings.get("subtitle_sentence_split", True)))
        self.spin_live_subtitle_max_lines.setValue(int(settings.get("subtitle_max_line_count", 2)))
        self.spin_live_subtitle_max_width.setValue(int(settings.get("subtitle_max_line_width", 64)))
        self.spin_live_gain.setValue(float(settings.get("gain", 1.0)))
        # Not the batch output folder: live sessions used to land there, and
        # nothing on the Live tab said so.
        self.live_output_dir.setText(str(settings.get("output_dir") or default_session_root()))
        if bool(settings.get("overlay_visible", False)):
            self.btn_live_overlay.setChecked(True)
            self._show_live_overlay()
        self._refresh_live_devices({
            CaptureSource.MIC: settings.get("mic_device_id"),
            CaptureSource.SYSTEM: settings.get("system_device_id"),
        })
        self._update_live_source_controls()
        self._update_live_export_controls()

    @staticmethod
    def _set_live_combo_value(combo, value) -> None:
        index = combo.findData(value)
        model = combo.model()
        item = model.item(index) if index >= 0 and hasattr(model, "item") else None
        # Сохранённый, но теперь недоступный пункт (Live estimate) не восстанавливаем.
        if index >= 0 and (item is None or item.isEnabled()):
            combo.setCurrentIndex(index)

    def _save_live_settings(self) -> None:
        self._live_settings = {
            "source": self.combo_live_source.currentData(),
            "mic_device_id": self._selected_live_device(CaptureSource.MIC),
            "system_device_id": self._selected_live_device(CaptureSource.SYSTEM),
            "record_mic_audio": self.cb_live_mic_audio.isChecked(),
            "record_system_audio": self.cb_live_system_audio.isChecked(),
            "export_txt": self.cb_live_export_txt.isChecked(),
            "export_txt_timecodes": self.cb_live_export_txt_timecodes.isChecked(),
            "export_txt_diarize": self.cb_live_export_txt_diarize.isChecked(),
            "export_txt_diarize_timecodes": self.cb_live_export_txt_diarize_timecodes.isChecked(),
            "export_md": self.cb_live_export_md.isChecked(),
            "export_srt": self.cb_live_export_srt.isChecked(),
            "export_vtt": self.cb_live_export_vtt.isChecked(),
            "subtitle_sentence_split": self.cb_live_subtitle_sentence_split.isChecked(),
            "subtitle_max_line_count": self.spin_live_subtitle_max_lines.value(),
            "subtitle_max_line_width": self.spin_live_subtitle_max_width.value(),
            "overlay_visible": bool(self.btn_live_overlay.isChecked()),
            "diarization_mode": self.combo_live_diarization.currentData(),
            "gain": self.spin_live_gain.value(),
            "output_dir": self.live_output_dir.text().strip(),
        }
        self.user_settings.set_value("live_settings", self._live_settings)

    def _selected_live_sources(self) -> set[CaptureSource]:
        source = self.combo_live_source.currentData()
        if source == "mic":
            return {CaptureSource.MIC}
        if source == "system":
            return {CaptureSource.SYSTEM}
        return {CaptureSource.MIC, CaptureSource.SYSTEM}

    def _update_live_source_controls(self) -> None:
        sources = self._selected_live_sources()
        self.cb_live_mic_audio.setEnabled(CaptureSource.MIC in sources)
        self.combo_live_mic_device.setEnabled(CaptureSource.MIC in sources)
        self.cb_live_system_audio.setEnabled(CaptureSource.SYSTEM in sources)
        self.combo_live_system_device.setEnabled(CaptureSource.SYSTEM in sources)

    def _update_live_export_controls(self, *_args) -> None:
        diarization_enabled = self.combo_live_diarization.currentData() != DiarizationMode.OFF.value
        for checkbox in (
            self.cb_live_export_txt_diarize,
            self.cb_live_export_txt_diarize_timecodes,
        ):
            if not diarization_enabled:
                signals_were_blocked = checkbox.blockSignals(True)
                checkbox.setChecked(False)
                checkbox.blockSignals(signals_were_blocked)
            checkbox.setEnabled(diarization_enabled)
        subtitles_enabled = self.cb_live_export_srt.isChecked() or self.cb_live_export_vtt.isChecked()
        for widget in (
            self.cb_live_subtitle_sentence_split,
            self.lbl_live_subtitle_max_lines,
            self.spin_live_subtitle_max_lines,
            self.lbl_live_subtitle_max_width,
            self.spin_live_subtitle_max_width,
        ):
            widget.setEnabled(subtitles_enabled)

    def _live_device_combos(self):
        return (
            (CaptureSource.MIC, self.combo_live_mic_device),
            (CaptureSource.SYSTEM, self.combo_live_system_device),
        )

    def _selected_live_device(self, source: CaptureSource) -> str | None:
        """Выбранное устройство; пока список перечитывается — то, что будет выбрано."""
        if self._live_devices_probing:
            return self._live_device_selection.get(source)
        combo = dict(self._live_device_combos())[source]
        return combo.currentData()

    def _refresh_live_devices(self, selected: dict | None = None) -> None:
        """Перечислить устройства захвата в фоновом потоке.

        ScreenCaptureKit отдаёт список системного звука через completion
        handler и ждёт его до 5 с; в Qt-потоке это дважды замораживало окно
        при каждом старте (вкладка Live и восстановление настроек).
        """
        if selected is None:
            selected = {source: self._selected_live_device(source) for source, _combo in self._live_device_combos()}
        self._live_device_selection = dict(selected)
        self._live_devices_probing = True
        self._live_devices_generation += 1
        generation = self._live_devices_generation
        signals = self.signals
        probe = self._probe_devices

        def work():
            found = {}
            for source in (CaptureSource.MIC, CaptureSource.SYSTEM):
                try:
                    found[source] = probe(source)
                except Exception:
                    found[source] = []
            try:
                signals.live_devices_probed.emit(generation, found)
            except RuntimeError:
                pass  # окно закрыли раньше, чем ответили устройства

        threading.Thread(target=work, name="live-devices", daemon=True).start()

    def _on_live_devices_probed(self, generation: int, found: dict) -> None:
        if generation != self._live_devices_generation:
            return  # ответ на прежний запрос: уже идёт новый
        self._live_devices_probing = False
        for source, combo in self._live_device_combos():
            devices = found.get(source) or []
            combo.clear()
            for device in devices:
                combo.addItem(device.name, device.id)
            index = combo.findData(self._live_device_selection.get(source))
            if index < 0:
                index = next((item for item, device in enumerate(devices) if device.is_default), 0)
            if combo.count():
                combo.setCurrentIndex(index)

    @staticmethod
    def _probe_devices(source: CaptureSource) -> list:
        """Enumerate devices and free the native handle straight away.

        Each probe adapter owns a PyAudio/WASAPI instance, so leaving them to
        the garbage collector leaked one native handle per device refresh.
        """
        probe = create_capture_adapter(sys.platform, source)
        try:
            return probe.devices()
        finally:
            release = getattr(probe, "release", None)
            if callable(release):
                release()

    def _select_live_output_folder(self) -> None:
        initial_dir = self.live_output_dir.text().strip() or self.output_dir or str(Path.home())
        selected = QFileDialog.getExistingDirectory(
            self,
            self._t("Выберите папку сессий", "Select session folder"),
            initial_dir,
        )
        if selected:
            self.live_output_dir.setText(selected)

    def _update_live_output_folder_label(self, path: str) -> None:
        """Show the folder of the current/last session, or where the next one goes."""
        session_dir = getattr(self, "_live_shown_session_dir", None)
        if session_dir is not None and Path(session_dir).parent != Path(path):
            self._live_shown_session_dir = session_dir = None
        if session_dir is not None:
            text = _display_path(session_dir)
        elif path:
            text = _display_path(path)
        else:
            text = self._t("Папка не выбрана", "Folder not selected")
        label = self.lbl_live_output_folder
        width = label.width()
        # One line, elided in the middle: a wrapped path overflowed the card
        # onto the buttons above it. The tooltip carries the full path.
        shown = (
            label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideMiddle, width)
            if label.isVisible() and width > 40 else text
        )
        label.setText(shown)
        label.setToolTip("\n".join(filter(None, (
            str(session_dir or path),
            self._t(
                "Каждая запись сохраняется в свою папку ГГГГ-ММ-ДД_ЧЧ-ММ-СС: "
                "транскрипт, субтитры и аудио.",
                "Each recording is saved to its own YYYY-MM-DD_HH-MM-SS folder: "
                "transcript, subtitles and audio.",
            ),
        ))))

    def _show_live_session_folder(self, session_dir: Path) -> None:
        self._live_shown_session_dir = Path(session_dir)
        self._update_live_output_folder_label(self.live_output_dir.text().strip())

    def _open_live_session_folder(self) -> None:
        session_dir = getattr(self, "_live_shown_session_dir", None)
        target = session_dir if session_dir is not None and session_dir.is_dir() else Path(
            self.live_output_dir.text().strip() or default_session_root()
        )
        if not target.is_dir():
            self.lbl_live_status.setText(self._t("Папка ещё не создана", "The folder does not exist yet"))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _start_live_session(self) -> None:
        if self._live_starting:
            # Модель ещё грузится: повторный клик не должен готовить вторую сессию.
            return
        session = self.live_session
        if session is not None and session.status().state is CaptureState.PAUSED:
            try:
                session.resume()
            except Exception as exc:  # noqa: BLE001 — исключение из Qt-слота роняет приложение
                self._report_live_problem(self._t(
                    f"Не удалось продолжить запись: {exc}",
                    f"Could not resume recording: {exc}",
                ))
            return
        output_dir = self.live_output_dir.text().strip() or str(default_session_root())
        if output_dir == str(default_session_root()):
            # The default lives in ~/Documents and is ours to create. A picked
            # folder that vanished (an unplugged drive) is not recreated.
            try:
                Path(output_dir).mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        if not Path(output_dir).is_dir():
            self.lbl_live_status.setText(
                self._t("Выберите существующую папку сессий", "Select an existing session folder")
            )
            return
        self._clear_live_problem()
        self._save_live_settings()
        request = self._live_start_request(Path(output_dir))
        if request is None:
            return
        self._live_starting = True
        self._update_live_control_state()
        self.lbl_live_status.setText(
            self._t(
                "Загрузка модели распознавания… Запись начнётся, когда она будет готова.",
                "Loading the recognition model… Recording starts once it is ready.",
            )
        )
        # Загрузка и прогрев модели — секунды (минуты при первом скачивании):
        # в Qt-потоке они замораживали окно, а processEvents() внутри
        # пропускал второй клик по «Начать запись».
        threading.Thread(
            target=self._prepare_live_backend, args=(request,), name="live-model-prepare", daemon=True,
        ).start()

    def _live_start_request(self, output_dir: Path) -> dict | None:
        """Снимок настроек Live на момент нажатия и адаптеры захвата."""
        sources = self._selected_live_sources()
        settings = LiveSettings(
            mic_device_id=self._selected_live_device(CaptureSource.MIC),
            system_device_id=self._selected_live_device(CaptureSource.SYSTEM),
            diarization_mode=DiarizationMode(self.combo_live_diarization.currentData()),
            record_mic_audio=CaptureSource.MIC in sources and self.cb_live_mic_audio.isChecked(),
            record_system_audio=CaptureSource.SYSTEM in sources and self.cb_live_system_audio.isChecked(),
            record_source_audio=any(
                checkbox.isChecked()
                for checkbox in (self.cb_live_mic_audio, self.cb_live_system_audio)
            ),
            record_mix_audio=sources == {CaptureSource.MIC, CaptureSource.SYSTEM},
        )
        try:
            adapters = {
                source: create_capture_adapter(
                    sys.platform,
                    source,
                    settings.mic_device_id if source is CaptureSource.MIC else settings.system_device_id,
                )
                for source in sources
            }
        except CaptureUnavailable as exc:
            self.lbl_live_status.setText(str(exc))
            self._report_live_problem(str(exc))
            return None
        export_selection = ExportSelection(
            txt=self.cb_live_export_txt.isChecked(),
            txt_timecodes=self.cb_live_export_txt_timecodes.isChecked(),
            txt_diarize=self.cb_live_export_txt_diarize.isChecked(),
            txt_diarize_timecodes=self.cb_live_export_txt_diarize_timecodes.isChecked(),
            md=self.cb_live_export_md.isChecked(),
            srt=self.cb_live_export_srt.isChecked(),
            vtt=self.cb_live_export_vtt.isChecked(),
            sentence_split=self.cb_live_subtitle_sentence_split.isChecked(),
            max_line_count=self.spin_live_subtitle_max_lines.value(),
            max_line_width=self.spin_live_subtitle_max_width.value(),
            sample_rate=settings.asr_sample_rate,
        )
        # One backend for every source: it serializes decodes on the shared model.
        backend = LazyModelBackend(
            self.model_loader,
            self._t("Не удалось загрузить модель распознавания", "Could not load recognition model"),
        )
        return {
            "output_dir": output_dir,
            "settings": settings,
            "adapters": adapters,
            "export_selection": export_selection,
            "backend": backend,
        }

    def _prepare_live_backend(self, request: dict) -> None:
        """Фоновый поток: загрузить и прогреть модель, итог — сигналом в Qt-поток."""
        error = None
        try:
            if self._preload_live_model():
                # Capture starts only once the model can recognise speech: the
                # first decode compiles kernels (seconds with MLX/CoreML), and
                # words spoken meanwhile waited for it.
                request["backend"].warm_up()
            else:
                detail = self.model_loader.diagnostics().get("error") or self._t("неизвестная ошибка", "unknown error")
                error = self._t(
                    f"Не удалось загрузить модель распознавания: {detail}",
                    f"Could not load the recognition model: {detail}",
                )
        except Exception as exc:  # noqa: BLE001 — сообщаем в интерфейс, а не в stderr потока
            error = self._t(
                f"Не удалось загрузить модель распознавания: {exc}",
                f"Could not load the recognition model: {exc}",
            )
        self.signals.live_backend_prepared.emit(request, error)

    def _on_live_backend_prepared(self, request: dict, error) -> None:
        self._live_starting = False
        if error or self._close_pending:
            self._release_live_adapters(request["adapters"])
            self.lbl_live_status.setText(self._t("Готово к записи", "Ready for live capture"))
            if error:
                self._report_live_problem(error)
            self._update_live_control_state()
            self._continue_pending_close()
            return
        backend = request["backend"]
        try:
            session = LiveSession(
                request["output_dir"],
                request["settings"],
                request["adapters"],
                scheduler_factory=lambda source, on_final, on_partial, on_error: LiveAsrScheduler(
                    backend,
                    on_final=on_final,
                    on_partial=on_partial,
                    on_error=on_error,
                ),
                export_selection=request["export_selection"],
                translate=self._t,
                log=self._log_live,
            )
        except Exception as exc:  # noqa: BLE001 — например, папку сессии не удалось создать
            self._release_live_adapters(request["adapters"])
            self.lbl_live_status.setText(self._t("Готово к записи", "Ready for live capture"))
            self._report_live_problem(self._t(
                f"Не удалось создать live-сессию: {exc}",
                f"Could not create the live session: {exc}",
            ))
            self._update_live_control_state()
            return
        self.live_session = session
        session.subscribe(self._on_live_session_update)
        try:
            session.start()
        except Exception as exc:  # noqa: BLE001 — захват не поднялся; Stop сохранит, что есть
            self._report_live_problem(self._t(
                f"Не удалось начать запись: {exc}",
                f"Could not start recording: {exc}",
            ))
            self._update_live_control_state()
            return
        self._show_live_session_folder(session.session_dir)

    @staticmethod
    def _release_live_adapters(adapters: dict) -> None:
        """Адаптеры, которые так и не попали в сессию, держат нативные хэндлы."""
        for adapter in adapters.values():
            release = getattr(adapter, "release", None)
            if callable(release):
                try:
                    release()
                except Exception:  # noqa: BLE001
                    pass

    def _log_live(self, message: str) -> None:
        """Route live-path diagnostics into the shared processing log tab."""
        self.log(f"[live] {message}")

    def _preload_live_model(self) -> bool:
        """Fail loudly before capture starts instead of decoding into a void.

        Runs on the live-model-prepare thread: no widget access here.
        """
        if self.model_loader.is_loaded():
            return True
        return bool(self.model_loader.load_model(logger=self.log))

    def _report_live_problem(self, message: str) -> None:
        self.lbl_live_problem.setText(message)
        self.lbl_live_problem.show()
        self.log(f"[live] {message}")

    def _clear_live_problem(self) -> None:
        self.lbl_live_problem.clear()
        self.lbl_live_problem.hide()

    def _pause_live_session(self) -> None:
        session = self.live_session
        if session is None or session.status().state is not CaptureState.RECORDING:
            return
        try:
            session.pause()
        except Exception as exc:  # noqa: BLE001 — например, PortAudioError при остановке потока
            self._report_live_problem(self._t(
                f"Не удалось поставить запись на паузу: {exc}",
                f"Could not pause recording: {exc}",
            ))

    def _stop_live_session(self) -> None:
        session = self.live_session
        if session is None:
            return
        if session.status().state not in (CaptureState.RECORDING, CaptureState.PAUSED, CaptureState.FAILED):
            return
        # Stopping now drains every queued decode, which can outlast a frame.
        # Run it off the Qt thread so the window stays responsive meanwhile.
        self.btn_live_stop.setEnabled(False)
        self.btn_live_pause.setEnabled(False)
        self.lbl_live_status.setText(
            self._t("Завершение расшифровки…", "Finishing transcription…")
        )
        self._live_stop_thread = threading.Thread(
            target=self._finish_live_session, args=(session,), daemon=True
        )
        self._live_stop_thread.start()

    def _finish_live_session(self, session) -> None:
        try:
            result = session.stop()
        except Exception as exc:
            # Раньше здесь уходил только статус: Start и Stop оставались
            # выключенными до перезапуска приложения.
            self.signals.live_stop_failed.emit(str(exc) or type(exc).__name__)
            return
        self.signals.live_finished.emit(result)

    def _on_live_stop_failed(self, detail: str) -> None:
        if self._live_llm_cancel_event is not None:
            self._live_llm_cancel_event.set()
        self._live_conversation_id = None
        # Сессия остановилась наполовину; спасать её нечем, а новая запись
        # должна быть доступна без перезапуска.
        self.live_session = None
        self.lbl_live_status.setText(self._t("Ошибка остановки", "Stop failed"))
        self._report_live_problem(self._t(
            f"Не удалось сохранить live-сессию: {detail}",
            f"Could not save the live session: {detail}",
        ))
        self._update_live_control_state(CaptureState.STOPPED)
        self._continue_pending_close()

    def _on_live_session_update(self, value) -> None:
        if isinstance(value, LiveStatus):
            self.signals.live_status.emit(value)
        else:
            self.signals.live_event.emit(value)

    def _show_live_overlay(self) -> None:
        if self.live_overlay is None:
            self.live_overlay = LiveOverlay(self)
            self.live_overlay.question_submitted.connect(self._answer_live_question)
            self.live_overlay.cancel_requested.connect(self._cancel_live_question)
            self.live_overlay.visibility_changed.connect(self._on_live_overlay_visibility)
        if self.live_session is not None and callable(getattr(self.live_session, "conversation", None)):
            self.live_overlay.set_conversation(self.live_session.conversation())
        self.live_overlay.show()
        self.live_overlay.raise_()

    def _hide_live_overlay(self) -> None:
        if self.live_overlay is not None:
            self.live_overlay.hide()

    def _toggle_live_overlay(self) -> None:
        """Mirror the frameless overlay's own close affordances on the tab button."""
        if self.btn_live_overlay.isChecked():
            self._show_live_overlay()
        else:
            self._hide_live_overlay()

    def _on_live_overlay_visibility(self, visible: bool) -> None:
        if self.btn_live_overlay.isChecked() != visible:
            blocked = self.btn_live_overlay.blockSignals(True)
            self.btn_live_overlay.setChecked(visible)
            self.btn_live_overlay.blockSignals(blocked)
        self._live_settings["overlay_visible"] = visible
        self.user_settings.set_value("live_settings", self._live_settings)

    def _update_live_event(self, event) -> None:
        if isinstance(event, TranscriptEvent):
            presenter = self._live_transcript_presenter
            delta = presenter.add_event(event)
            if delta:
                piece = presenter.rendered_delta(event, delta)
                if presenter.rewrote:
                    self._redraw_live_transcript(presenter.rendered_pieces())
                else:
                    self._append_live_transcript(piece)
        elif isinstance(event, CaptureEvent):
            self._show_live_capture_status(event)
        self._update_live_overlay(event)

    def _show_live_capture_status(self, event: CaptureEvent) -> None:
        key = (event.source, event.detail)
        now = time.monotonic()
        if now - self._live_capture_status_times.get(key, float("-inf")) < 5:
            return
        self._live_capture_status_times[key] = now
        self.lbl_live_status.setText(event.detail)
        # State updates overwrite the status line, so problems get their own
        # banner that survives until the next session starts.
        if event.kind is not CaptureEventKind.DISCONTINUITY:
            self.lbl_live_problem.setText(event.detail)
            self.lbl_live_problem.show()

    def _append_live_transcript(self, text: str) -> None:
        scrollbar = self.live_transcript.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 2
        position = scrollbar.value()
        cursor = self.live_transcript.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(f"{text}\n")
        scrollbar.setValue(scrollbar.maximum() if at_bottom else position)

    def _redraw_live_transcript(self, pieces: list[str]) -> None:
        scrollbar = self.live_transcript.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 2
        position = scrollbar.value()
        self.live_transcript.setPlainText("".join(f"{piece}\n" for piece in pieces))
        scrollbar.setValue(scrollbar.maximum() if at_bottom else position)

    def _clear_live_display(self) -> None:
        self.live_transcript.clear()
        self._live_transcript_presenter.clear()
        if self.live_session is not None and self.live_session.status().state not in (CaptureState.STOPPED, CaptureState.IDLE):
            try:
                self.live_session.clear_conversation()
            except RuntimeError:
                pass  # разговор уже заморожен остановкой — чистить нечего
        if self.live_overlay is not None:
            self.live_overlay.clear_transcript()
            self.live_overlay.set_conversation(
                self.live_session.conversation() if self.live_session is not None else []
            )

    def _update_live_overlay(self, event) -> None:
        if self.live_overlay is not None and isinstance(event, TranscriptEvent):
            self.live_overlay.update_transcript(event)

    def _answer_live_question(self, question: str) -> None:
        if self.live_session is None:
            self._update_live_answer(
                "error",
                self._t(
                    "Сначала запустите live-сессию, прежде чем задавать вопрос ассистенту.",
                    "Start a live session before asking the assistant.",
                ),
            )
            return
        transcript = self.live_session.ask_context()
        if not transcript:
            self._update_live_answer(
                "error",
                self._t(
                    "Пока нет финальных событий расшифровки.",
                    "No final transcript events are available yet.",
                ),
            )
            return
        try:
            llm_settings = self._collect_llm_settings()
        except ValueError as exc:
            self._update_live_answer(
                "error",
                self._t(f"LLM не настроена: {exc}", f"LLM is not configured: {exc}"),
            )
            return
        stopped = self._t(
            "Live-сессия уже остановлена: вопросы ассистенту принимаются во время записи.",
            "The live session has stopped: the assistant takes questions while recording.",
        )
        if self.live_session.status().state in (CaptureState.STOPPING, CaptureState.STOPPED):
            self._update_live_answer("error", stopped)
            return
        try:
            turn = self.live_session.begin_conversation(question)
        except RuntimeError:
            # stop() замораживает разговор; исключение из Qt-слота роняет приложение.
            self._update_live_answer("error", stopped)
            return
        self._live_conversation_id = turn.id
        self._sync_live_conversation()
        cancel_event = threading.Event()
        self._live_llm_cancel_event = cancel_event
        threading.Thread(
            target=self._run_live_question,
            args=(llm_settings, transcript, question, cancel_event),
            daemon=True,
        ).start()

    def _run_live_question(
        self, llm_settings: dict, transcript: str, question: str, cancel_event: threading.Event | None = None,
    ) -> None:
        cancel_event = cancel_event or threading.Event()
        try:
            answer = self._run_llm_provider(
                llm_settings,
                transcript,
                question,
                on_stream_chunk=lambda chunk: self.signals.live_answer.emit("chunk", chunk),
                cancel_check=cancel_event.is_set,
            )
        except Exception as exc:
            if cancel_event.is_set():
                return
            detail = self._live_llm_error_detail(str(exc), llm_settings)
            self.signals.live_answer.emit(
                "error",
                self._t(f"Ошибка LLM: {detail}", f"LLM error: {detail}"),
            )
        else:
            if not cancel_event.is_set():
                self.signals.live_answer.emit("answer", answer)
        finally:
            if self._live_llm_cancel_event is cancel_event:
                self._live_llm_cancel_event = None

    def _cancel_live_question(self) -> None:
        if self._live_llm_cancel_event is not None:
            self._live_llm_cancel_event.set()
        if self.live_session is not None and self._live_conversation_id is not None:
            self.live_session.cancel_conversation(self._live_conversation_id)
            self._live_conversation_id = None
            self._sync_live_conversation()

    def _live_llm_error_detail(self, error: str, llm_settings: dict) -> str:
        diagnostic = (error or "").strip()
        api_key = llm_settings.get("api_key", "")
        if api_key:
            diagnostic = diagnostic.replace(api_key, "[redacted]")
        diagnostic = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[redacted]", diagnostic)
        compact = self._compact_llm_error(diagnostic)
        normalized = " ".join(diagnostic.split())
        if normalized and normalized not in compact:
            return f"{compact}: {normalized[:300]}"
        return compact

    def _update_live_answer(self, status: str, text: str) -> None:
        if self.live_session is not None and self._live_conversation_id is not None:
            try:
                if status == "chunk":
                    self.live_session.append_conversation_answer(self._live_conversation_id, text)
                else:
                    self.live_session.finish_conversation(
                        self._live_conversation_id,
                        text,
                        status="error" if status == "error" else "complete",
                    )
                    self._live_conversation_id = None
            except RuntimeError:
                # The session stopped and froze its conversation while the
                # answer was in flight. An exception escaping a Qt slot aborts
                # the whole application, so the late answer is dropped instead.
                self._live_conversation_id = None
            self._sync_live_conversation()
            return
        if self.live_overlay is not None:
            if status == "chunk":
                self.live_overlay.append_answer(text)
            else:
                self.live_overlay.set_answer(text)
                self.live_overlay.finish_generation()

    def _sync_live_conversation(self) -> None:
        if self.live_overlay is not None and self.live_session is not None:
            self.live_overlay.set_conversation(self.live_session.conversation())
            if self._live_conversation_id is None:
                self.live_overlay.finish_generation()

    def _update_live_status(self, status: LiveStatus) -> None:
        labels = {
            CaptureState.IDLE: self._t("Ожидание", "Idle"),
            CaptureState.STARTING: self._t("Запуск", "Starting"),
            CaptureState.RECORDING: self._t("Идёт запись", "Recording"),
            CaptureState.PAUSED: self._t("На паузе", "Paused"),
            CaptureState.STOPPING: self._t("Остановка", "Stopping"),
            CaptureState.STOPPED: self._t("Остановлено", "Stopped"),
            CaptureState.FAILED: self._t("Ошибка", "Failed"),
        }
        self.lbl_live_status.setText(labels[status.state])
        self._update_live_control_state(status.state)

    def _on_live_finished(self, result) -> None:
        self._last_result_dir = str(result.session_dir)
        # A question still in flight belongs to a conversation that stop() froze.
        if self._live_llm_cancel_event is not None:
            self._live_llm_cancel_event.set()
        self._live_conversation_id = None
        self._show_live_session_folder(result.session_dir)
        self.lbl_live_status.setText(
            self._t("Сохранено: ", "Saved: ") + Path(result.session_dir).name
        )
        self.lbl_live_status.setToolTip(str(result.session_dir))
        self._update_live_control_state(CaptureState.STOPPED)
        self._sync_live_conversation()
        self._continue_pending_close()

    def _update_live_control_state(self, state: CaptureState | None = None) -> None:
        state = state or (self.live_session.status().state if self.live_session else CaptureState.IDLE)
        if self._live_starting:
            # Модель для новой сессии ещё грузится: ни второго старта, ни стопа.
            state = CaptureState.STARTING
        # Not FAILED: starting over a failed session abandoned it unstopped —
        # no exports, open FLAC writers, scheduler threads left running. Stop
        # (enabled in FAILED) saves what there is first.
        self.btn_live_start.setEnabled(state in {CaptureState.IDLE, CaptureState.PAUSED, CaptureState.STOPPED})
        self.btn_live_start.setText(
            self._t("ПРОДОЛЖИТЬ", "RESUME")
            if state is CaptureState.PAUSED
            else self._t("НАЧАТЬ ЗАПИСЬ", "START LIVE")
        )
        self.btn_live_pause.setEnabled(state is CaptureState.RECORDING)
        self.btn_live_stop.setEnabled(state in {CaptureState.RECORDING, CaptureState.PAUSED, CaptureState.FAILED})
