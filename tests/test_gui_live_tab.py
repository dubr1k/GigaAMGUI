import os
import sys
import threading
import time
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

sys.modules.setdefault("gigaam", types.SimpleNamespace(load_model=lambda *args, **kwargs: object()))
sys.modules.setdefault("yt_dlp", types.SimpleNamespace(YoutubeDL=object))

from src.gui.app_qt import GigaTranscriberQtApp  # noqa: E402
from src.live.types import (  # noqa: E402
    CaptureDevice,
    CaptureEvent,
    CaptureEventKind,
    CaptureSource,
    TranscriptEvent,
)


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


def _wait_for_live_stop(window, timeout=5.0):
    """Stopping drains queued decodes off the Qt thread; wait it out."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        thread = window._live_stop_thread
        if thread is not None and not thread.is_alive():
            QApplication.processEvents()
            return
        time.sleep(0.01)
    raise AssertionError("live session did not finish stopping")


@pytest.fixture
def window(qapp, request):
    instance = GigaTranscriberQtApp()
    yield instance
    if instance.live_session is not None and instance.live_session.status().state.value in {
        "recording", "paused", "failed",
    }:
        instance.live_session.stop()
    if request.node.name == "test_live_uses_actual_default_device_and_live_scheduler":
        instance.deleteLater()
        qapp.processEvents()
    else:
        instance.close()


def test_live_source_disables_unselected_track_without_clearing_it(window):
    window.combo_live_source.setCurrentIndex(window.combo_live_source.findData("both"))
    window.combo_live_source.setCurrentIndex(window.combo_live_source.findData("mic"))

    assert window.cb_live_mic_audio.isEnabled() is True
    assert window.cb_live_system_audio.isEnabled() is False
    assert window.cb_live_system_audio.isChecked() is True


def test_live_start_passes_exact_selected_audio_tracks_to_session(window, tmp_path, monkeypatch):
    from src.live.capture.noop import NoOpCaptureAdapter

    monkeypatch.setattr(
        "src.gui.live_mixin.create_capture_adapter",
        lambda platform, source, device_id: NoOpCaptureAdapter(source, device_id),
    )
    window.combo_live_source.setCurrentIndex(window.combo_live_source.findData("both"))
    window.cb_live_mic_audio.setChecked(False)
    window.cb_live_system_audio.setChecked(True)
    window.live_output_dir.setText(str(tmp_path))

    window._start_live_session()

    assert window.live_session._settings.record_mic_audio is False
    assert window.live_session._settings.record_system_audio is True
    window._stop_live_session()



def test_live_start_surfaces_missing_capture_runtime(window, tmp_path, monkeypatch):
    from src.live.capture.factory import CaptureUnavailable

    def unavailable(*_args):
        raise CaptureUnavailable("Windows live capture requires PyAudioWPatch.")

    monkeypatch.setattr("src.gui.live_mixin.create_capture_adapter", unavailable)
    window.live_output_dir.setText(str(tmp_path))

    window._start_live_session()

    assert window.live_session is None
    assert "PyAudioWPatch" in window.lbl_live_status.text()

def test_live_settings_persist_across_windows(qapp, tmp_path):
    first = GigaTranscriberQtApp()
    first.combo_live_source.setCurrentIndex(first.combo_live_source.findData("both"))
    first.cb_live_export_txt.setChecked(False)
    first.cb_live_export_md.setChecked(True)
    first.combo_live_diarization.setCurrentIndex(first.combo_live_diarization.findData("after_stop"))
    first.cb_live_export_txt_diarize.setChecked(True)
    first.cb_live_subtitle_sentence_split.setChecked(False)
    first.spin_live_subtitle_max_lines.setValue(3)
    first.spin_live_subtitle_max_width.setValue(48)
    first.live_output_dir.setText(str(tmp_path / "sessions"))
    first._save_live_settings()
    first.close()

    restored = GigaTranscriberQtApp()
    try:
        assert restored.combo_live_source.currentData() == "both"
        assert restored.combo_live_system_device.findData("system-default") == -1
        assert restored.cb_live_export_txt.isChecked() is False
        assert restored.cb_live_export_md.isChecked() is True
        assert restored.cb_live_export_txt_diarize.isChecked() is True
        assert restored.cb_live_subtitle_sentence_split.isChecked() is False
        assert restored.spin_live_subtitle_max_lines.value() == 3
        assert restored.spin_live_subtitle_max_width.value() == 48
        assert restored.live_output_dir.text() == str(tmp_path / "sessions")
    finally:
        restored.close()


def test_live_controls_drive_injected_capture_session_lifecycle(window, tmp_path, monkeypatch):
    from src.live.capture.noop import NoOpCaptureAdapter

    monkeypatch.setattr(
        "src.gui.live_mixin.create_capture_adapter",
        lambda platform, source, device_id: NoOpCaptureAdapter(source, device_id),
    )
    window.live_output_dir.setText(str(tmp_path))

    window._start_live_session()
    assert window.live_session.status().state.value == "recording"
    assert window.btn_live_start.isEnabled() is False
    assert window.btn_live_start.text() == "НАЧАТЬ ЗАПИСЬ"
    assert window.btn_live_pause.isEnabled() is True
    assert window.btn_live_stop.isEnabled() is True

    window._pause_live_session()
    assert window.live_session.status().state.value == "paused"
    assert window.btn_live_start.isEnabled() is True
    assert window.btn_live_start.text() == "ПРОДОЛЖИТЬ"
    assert window.btn_live_pause.isEnabled() is False

    window._start_live_session()
    assert window.live_session.status().state.value == "recording"
    assert window.btn_live_start.isEnabled() is False
    assert window.btn_live_start.text() == "НАЧАТЬ ЗАПИСЬ"
    assert window.btn_live_pause.isEnabled() is True

    window._stop_live_session()
    _wait_for_live_stop(window)
    assert window.live_session.status().state.value == "stopped"
    assert window.btn_live_start.isEnabled() is True
    assert window.btn_live_stop.isEnabled() is False


def test_live_overlay_receives_transcript_events_and_hides_without_destroying(window):
    window._show_live_overlay()
    event = TranscriptEvent(
        event_id="event-1",
        revision=0,
        source=CaptureSource.MIC,
        sample_start=0,
        sample_end=16000,
        timestamp_ns=0,
        text="Final line",
        status="final",
        speaker="Speaker 1",
    )

    window.signals.live_event.emit(event)

    assert window.live_overlay.isVisible() is True
    assert "Final line" in window.live_overlay.final_text.toPlainText()
    window.live_overlay.close()
    assert window.live_overlay.isVisible() is False
    assert window.live_overlay is not None


def test_live_overlay_reports_missing_llm_configuration_without_blocking(window, tmp_path, monkeypatch):
    from src.live.capture.noop import NoOpCaptureAdapter

    monkeypatch.setattr(
        "src.gui.live_mixin.create_capture_adapter",
        lambda platform, source, device_id: NoOpCaptureAdapter(source, device_id),
    )
    window.live_output_dir.setText(str(tmp_path))
    window._start_live_session()
    window.live_session._on_final(TranscriptEvent(
        event_id="event-1",
        revision=0,
        source=CaptureSource.MIC,
        sample_start=0,
        sample_end=16000,
        timestamp_ns=1_000_000_000,
        text="Final line",
        status="final",
    ))
    window._show_live_overlay()
    window.live_overlay.question_input.setText("What was said?")
    window.live_overlay.send_button.click()

    assert "LLM не настроена:" in window.live_overlay.answer_text.toPlainText()
    assert window.live_overlay.send_button.isEnabled() is True
    window._stop_live_session()


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("ru", "Сначала запустите live-сессию, прежде чем задавать вопрос ассистенту."),
        ("en", "Start a live session before asking the assistant."),
    ],
)
def test_live_question_requires_an_active_session_in_the_selected_language(window, language, expected):
    window._lang = language
    window._show_live_overlay()

    window._answer_live_question("What was said?")

    assert window.live_overlay.answer_text.toPlainText() == expected


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("ru", "Пока нет финальных событий расшифровки."),
        ("en", "No final transcript events are available yet."),
    ],
)
def test_live_question_requires_a_final_transcript_in_the_selected_language(
    window, language, expected, monkeypatch,
):
    window._lang = language
    monkeypatch.setattr(
        window,
        "live_session",
        types.SimpleNamespace(
            ask_context=lambda: "",
            status=lambda: types.SimpleNamespace(state=types.SimpleNamespace(value="stopped")),
        ),
    )
    window._show_live_overlay()

    window._answer_live_question("What was said?")

    assert window.live_overlay.answer_text.toPlainText() == expected


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("ru", "LLM не настроена: missing key"),
        ("en", "LLM is not configured: missing key"),
    ],
)
def test_live_question_reports_missing_llm_configuration_in_the_selected_language(
    window, language, expected, monkeypatch,
):
    window._lang = language
    monkeypatch.setattr(
        window,
        "live_session",
        types.SimpleNamespace(
            ask_context=lambda: "Final text",
            status=lambda: types.SimpleNamespace(state=types.SimpleNamespace(value="stopped")),
        ),
    )
    monkeypatch.setattr(
        window,
        "_collect_llm_settings",
        lambda: (_ for _ in ()).throw(ValueError("missing key")),
    )
    window._show_live_overlay()

    window._answer_live_question("What was said?")

    assert window.live_overlay.answer_text.toPlainText() == expected


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("ru", "Ошибка LLM: connection failed"),
        ("en", "LLM error: connection failed"),
    ],
)
def test_live_question_reports_provider_errors_in_the_selected_language(
    window, language, expected, monkeypatch,
):
    window._lang = language
    monkeypatch.setattr(
        window,
        "_run_llm_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("connection failed")),
    )
    window._show_live_overlay()

    window._run_live_question({}, "Final text", "What was said?")

    assert window.live_overlay.answer_text.toPlainText() == expected


def test_live_question_keeps_provider_diagnostic_without_secret(window, monkeypatch):
    monkeypatch.setattr(
        window,
        "_run_llm_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError(
                "Failed to refresh available models: provider supplied missing field "
                "`base_instructions`; Bearer super-secret"
            )
        ),
    )
    window._show_live_overlay()

    window._run_live_question({"api_key": "super-secret"}, "Final text", "What was said?")

    displayed = window.live_overlay.answer_text.toPlainText()
    assert "missing field `base_instructions`" in displayed
    assert "super-secret" not in displayed


def test_cancelling_live_question_keeps_capture_running(window, tmp_path, monkeypatch):
    from src.live.capture.noop import NoOpCaptureAdapter

    monkeypatch.setattr(
        "src.gui.live_mixin.create_capture_adapter",
        lambda _platform, source, device_id: NoOpCaptureAdapter(source, device_id),
    )
    window.live_output_dir.setText(str(tmp_path))
    window._start_live_session()
    window.live_session._on_final(TranscriptEvent(
        "event-1", 0, CaptureSource.MIC, 0, 16_000, 1_000_000_000, "Final line", "final",
    ))
    started = threading.Event()

    def wait_for_cancel(*_args, cancel_check=None, **_kwargs):
        started.set()
        while not cancel_check():
            time.sleep(0.01)
        raise RuntimeError("request cancelled")

    monkeypatch.setattr(window, "_collect_llm_settings", lambda: {"api_key": "key"})
    monkeypatch.setattr(window, "_run_llm_provider", wait_for_cancel)
    window._show_live_overlay()
    window.live_overlay.question_input.setText("What was said?")
    window.live_overlay.send_button.click()
    assert started.wait(1)

    window.live_overlay.cancel_button.click()

    assert window.live_session.status().state.value == "recording"
    assert "What was said?" in window.live_overlay.answer_text.toPlainText()
    window._stop_live_session()


def test_live_uses_actual_default_device_and_live_scheduler(window, tmp_path, monkeypatch):
    from src.live.capture.noop import NoOpCaptureAdapter

    class LoadedModel:
        def is_loaded(self):
            return True

        def transcribe_window(self, audio, sample_rate, offset_samples):
            return []

    class Scheduler:
        def __init__(self, backend, *, on_final, on_partial, on_error):
            self.backend = backend

        def submit(self, chunk):
            pass

        def flush(self):
            pass

        def close(self):
            pass

    class Session:
        def __init__(self, _root, _settings, _adapters, *, scheduler_factory, **_kwargs):
            self._schedulers = {
                CaptureSource.MIC: scheduler_factory(
                    CaptureSource.MIC, lambda _event: None, lambda _event: None, lambda _error: None,
                )
            }

        def subscribe(self, _callback):
            pass

        def start(self):
            pass

        def status(self):
            return types.SimpleNamespace(state=types.SimpleNamespace(value="stopped"))

    def adapter(_platform, source, device_id=None):
        result = NoOpCaptureAdapter(source, device_id)
        result.devices = lambda: [
            CaptureDevice("default-mic", "Built-in microphone", source, 48_000, 1, True),
            CaptureDevice("usb-mic", "USB microphone", source, 48_000, 1, False),
        ]
        return result

    monkeypatch.setattr("src.gui.live_mixin.create_capture_adapter", adapter)
    monkeypatch.setattr("src.gui.live_mixin.LiveAsrScheduler", Scheduler)
    monkeypatch.setattr("src.gui.live_mixin.LiveSession", Session)
    window.model_loader = LoadedModel()
    window._refresh_live_devices()
    window.live_output_dir.setText(str(tmp_path))

    window._start_live_session()

    assert window.combo_live_mic_device.currentData() == "default-mic"
    assert window.combo_live_mic_device.findData("mic-default") == -1
    assert isinstance(window.live_session._schedulers[CaptureSource.MIC], Scheduler)


def test_live_rejects_missing_output_folder_and_keeps_capture_events_out_of_transcript(window, tmp_path):
    missing = tmp_path / "missing"
    window.live_output_dir.setText(str(missing))

    window._start_live_session()

    assert window.live_session is None
    assert any(
        label in window.lbl_live_status.text().lower()
        for label in ("folder", "папку")
    )
    event = CaptureEvent(
        CaptureEventKind.PERMISSION_DENIED,
        CaptureSource.MIC,
        0,
        0,
        "Microphone permission denied",
    )
    window._update_live_event(event)

    assert "Microphone permission denied" not in window.live_transcript.toPlainText()
    assert "Microphone permission denied" in window.lbl_live_status.text()


def test_live_capture_status_is_throttled_in_non_modal_banner(window, monkeypatch):
    updates = []
    monkeypatch.setattr(window.lbl_live_status, "setText", lambda text: updates.append(text))
    event = CaptureEvent(CaptureEventKind.STATUS, CaptureSource.MIC, 0, 0, "Capture warning")

    window._update_live_event(event)
    window._update_live_event(event)

    assert updates == ["Capture warning"]
    assert window.live_transcript.toPlainText() == ""


def test_live_tab_shows_the_current_partial_tail_without_rewriting_history(window, qapp):
    for number in range(30):
        window._update_live_event(TranscriptEvent(
            f"final-{number}", 0, CaptureSource.MIC, number, number + 1, 0,
            f"Final line {number}", "final",
        ))
    qapp.processEvents()
    scrollbar = window.live_transcript.verticalScrollBar()
    scrollbar.setValue(0)
    window._update_live_event(TranscriptEvent(
        "partial", 0, CaptureSource.MIC, 0, 1, 0, "First partial", "partial",
    ))
    window._update_live_event(TranscriptEvent(
        "partial", 1, CaptureSource.MIC, 0, 1, 0, "Revised partial", "partial",
        supersedes=0,
    ))

    transcript = window.live_transcript.toPlainText()
    assert "First partial" in transcript
    assert "Revised partial" not in transcript
    assert scrollbar.value() == 0

    scrollbar.setValue(scrollbar.maximum())
    window._update_live_event(TranscriptEvent(
        "new-final", 0, CaptureSource.MIC, 0, 1, 0, "Following newest", "final",
    ))
    assert scrollbar.value() == scrollbar.maximum()


def test_live_tab_and_overlay_share_append_only_transcript_history(window):
    window._show_live_overlay()
    events = [
        TranscriptEvent("one", 0, CaptureSource.MIC, 0, 1, 0, "First sentence.", "final"),
        TranscriptEvent("two", 0, CaptureSource.MIC, 1, 2, 0, "Second sentence.", "final"),
        TranscriptEvent(
            "three", 0, CaptureSource.MIC, 2, 3, 0, "Other speaker.", "final", speaker="Speaker 2",
        ),
    ]
    for event in events:
        window._update_live_event(event)
    window._update_live_event(
        TranscriptEvent("partial", 0, CaptureSource.MIC, 3, 4, 0, "First partial", "partial")
    )
    window._update_live_event(
        TranscriptEvent("partial", 1, CaptureSource.MIC, 3, 4, 0, "Revised partial", "partial", supersedes=0)
    )

    tab_text = window.live_transcript.toPlainText()
    overlay_text = window.live_overlay.final_text.toPlainText()
    assert tab_text.strip() == overlay_text.strip()
    assert "MIC" in tab_text
    assert "Speaker 2" in tab_text
    assert "First partial" in tab_text
    assert "Revised partial" not in tab_text
    assert "First partial" in overlay_text
    assert "Revised partial" not in overlay_text


def test_clear_live_display_keeps_active_session_and_saved_transcript(window, tmp_path, monkeypatch):
    from src.live.capture.noop import NoOpCaptureAdapter

    monkeypatch.setattr(
        "src.gui.live_mixin.create_capture_adapter",
        lambda _platform, source, device_id: NoOpCaptureAdapter(source, device_id),
    )
    window.live_output_dir.setText(str(tmp_path))
    window._start_live_session()
    window._show_live_overlay()
    event = TranscriptEvent(
        "event-1", 0, CaptureSource.MIC, 0, 16_000, 1_000_000_000,
        "Saved final text", "final",
    )
    window.live_session._on_final(event)
    turn = window.live_session.begin_conversation("What was said?")
    window.live_session.finish_conversation(turn.id, "Saved answer")
    window.live_overlay.set_conversation(window.live_session.conversation())
    window._update_live_event(TranscriptEvent(
        "partial-1", 0, CaptureSource.MIC, 16_000, 32_000, 1_000_000_000,
        "Visible partial", "partial",
    ))
    session = window.live_session

    window.btn_live_clear.click()

    assert window.live_session is session
    assert window.live_session.status().state.value == "recording"
    assert window.live_session.ask_context().endswith("Saved final text")
    assert window.live_transcript.toPlainText() == ""
    assert window.live_overlay.final_text.toPlainText() == ""
    assert window.live_overlay.partial_label.text() == ""
    assert window.live_session.conversation() == []
    assert window.live_overlay.answer_text.toPlainText() == ""


def test_live_folder_picker_updates_reused_folder_display(window, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.gui.live_mixin.QFileDialog.getExistingDirectory",
        lambda *_args: str(tmp_path),
    )

    window._select_live_output_folder()

    assert window.live_output_dir.text() == str(tmp_path)
    assert str(tmp_path) in window.lbl_live_output_folder.text()


def test_live_formats_match_processing_fields_and_order(window):
    assert window.grp_live_output.title().startswith("2.")
    assert window.grp_live_exports.title().startswith("3.")
    assert [
        checkbox.text()
        for checkbox in (
            window.cb_live_export_txt,
            window.cb_live_export_txt_timecodes,
            window.cb_live_export_txt_diarize,
            window.cb_live_export_txt_diarize_timecodes,
            window.cb_live_export_md,
            window.cb_live_export_srt,
            window.cb_live_export_vtt,
        )
    ] == [
        "Текст",
        "Таймкоды",
        "Диар.",
        "Диар. + время",
        "Markdown",
        "SRT",
        "VTT",
    ]
    assert window.cb_live_subtitle_sentence_split.isChecked() is True
    assert window.spin_live_subtitle_max_lines.value() == 2
    assert window.spin_live_subtitle_max_width.value() == 64


def test_live_diarized_formats_clear_when_diarization_is_unavailable(window):
    window.combo_live_diarization.setCurrentIndex(window.combo_live_diarization.findData("after_stop"))
    window.cb_live_export_txt_diarize.setChecked(True)
    window.combo_live_diarization.setCurrentIndex(window.combo_live_diarization.findData("off"))

    assert window.cb_live_export_txt_diarize.isEnabled() is False
    assert window.cb_live_export_txt_diarize.isChecked() is False


def test_live_language_toggle_translates_every_live_control(window):
    window._toggle_language()

    assert window.grp_live_source.title() == "1. Live capture"
    assert window.grp_live_output.title() == "2. Session folder"
    assert window.grp_live_exports.title() == "3. Output formats"
    assert window.btn_live_output_select.text() == "Choose folder"
    assert window.cb_live_export_txt.text() == "Text"
    assert window.cb_live_export_txt_timecodes.text() == "Timecodes"
    assert window.cb_live_export_txt_diarize.text() == "Diar."
    assert window.cb_live_export_txt_diarize_timecodes.text() == "Diar. + time"
    assert window.cb_live_export_md.text() == "Markdown"
    assert window.cb_live_subtitle_sentence_split.text() == "By sentences"
    assert window.lbl_live_subtitle_max_lines.text() == "Lines:"
    assert window.lbl_live_subtitle_max_width.text() == "Characters:"
    assert window.cb_live_mic_audio.text() == "Record microphone track"
    assert window.cb_live_system_audio.text() == "Record system audio track"
    assert window.btn_live_start.text() == "START LIVE"
    assert window.btn_live_pause.text() == "Pause"
    assert window.btn_live_stop.text() == "Stop"
    assert window.btn_live_clear.text() == "Clear"
    assert window.btn_live_overlay.text() == "Overlay"
    assert window.combo_live_source.itemText(0) == "Microphone"
