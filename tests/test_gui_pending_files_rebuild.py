import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PyQt6 = pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from src.gui.app_qt import GigaTranscriberQtApp  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_gui_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGAAM_CONFIG_DIR", str(tmp_path / "config"))


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    win = GigaTranscriberQtApp()
    yield win
    win.close()
    app.processEvents()


def test_rebuild_pending_audio_files_skips_already_transcribed(tmp_path, window):
    input_dir = tmp_path / "audio"
    input_dir.mkdir()
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (input_dir / "done.wav").write_bytes(b"")
    (input_dir / "new.wav").write_bytes(b"")
    (output_dir / "done.txt").write_text("transcript")

    window.input_dir = str(input_dir)
    window.output_dir = str(output_dir)
    window.output_formats["txt"] = True

    window._rebuild_pending_audio_files()

    assert window.files_to_process == [str(input_dir / "new.wav")]


def test_rebuild_pending_audio_files_no_output_dir_keeps_all(tmp_path, window):
    input_dir = tmp_path / "audio"
    input_dir.mkdir()
    (input_dir / "a.wav").write_bytes(b"")
    (input_dir / "b.mp3").write_bytes(b"")

    window.input_dir = str(input_dir)
    window.output_dir = ""

    window._rebuild_pending_audio_files()

    assert sorted(window.files_to_process) == sorted(
        [str(input_dir / "a.wav"), str(input_dir / "b.mp3")]
    )


def test_rebuild_pending_audio_files_missing_folder_is_empty(tmp_path, window):
    window.input_dir = str(tmp_path / "does-not-exist")
    window._rebuild_pending_audio_files()
    assert window.files_to_process == []


def test_rebuild_pending_llm_transcripts_skips_already_processed(tmp_path, window):
    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir()
    (transcript_dir / "meeting.txt").write_text("text")
    (transcript_dir / "other.txt").write_text("text")
    (transcript_dir / "meeting_llm_summary.txt").write_text("summary")

    window.llm_transcript_dir = str(transcript_dir)
    window.llm_output_dir = str(transcript_dir)
    for key, cb in window.llm_action_checkboxes.items():
        cb.setChecked(key == "summary")
    for key, cb in window.llm_export_checkboxes.items():
        cb.setChecked(key == "txt")

    window._rebuild_pending_llm_transcripts()

    assert window.transcript_files_for_llm == [str(transcript_dir / "other.txt")]


def test_rebuild_pending_llm_transcripts_ignores_llm_output_files(tmp_path, window):
    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir()
    (transcript_dir / "note.txt").write_text("text")
    (transcript_dir / "note_llm_tasks.txt").write_text("tasks")

    window.llm_transcript_dir = str(transcript_dir)
    window.llm_output_dir = str(transcript_dir)
    for cb in window.llm_action_checkboxes.values():
        cb.setChecked(False)
    for cb in window.llm_export_checkboxes.values():
        cb.setChecked(False)

    window._rebuild_pending_llm_transcripts()

    assert window.transcript_files_for_llm == [str(transcript_dir / "note.txt")]


def test_clearing_files_list_forgets_input_dir_so_restart_stays_empty(tmp_path, window):
    input_dir = tmp_path / "audio"
    input_dir.mkdir()
    (input_dir / "a.wav").write_bytes(b"")

    window.input_dir = str(input_dir)
    window.files_to_process = [str(input_dir / "a.wav")]

    window._clear_files_list()

    assert window.files_to_process == []
    assert window.input_dir == ""
    assert window.user_settings.get_last_files_dir() is None

    # Simulate a restart: rebuilding must not resurrect files from the old folder.
    window._rebuild_pending_audio_files()
    assert window.files_to_process == []


def test_clearing_llm_files_list_forgets_transcript_dir_so_restart_stays_empty(tmp_path, window):
    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir()
    (transcript_dir / "note.txt").write_text("text")

    window.llm_transcript_dir = str(transcript_dir)
    window.transcript_files_for_llm = [str(transcript_dir / "note.txt")]

    window._clear_llm_files_list()

    assert window.transcript_files_for_llm == []
    assert window.user_settings.get_value("llm_transcript_dir", "") == ""

    window._rebuild_pending_llm_transcripts()
    assert window.transcript_files_for_llm == []


def test_clearing_forgets_input_dir_even_when_the_queue_is_already_empty(tmp_path, window):
    """Состояние сразу после перезапуска: очередь пуста, папка запомнена.

    Прежний guard выходил на `not self.files_to_process`, поэтому «Очистить»
    в этом состоянии не делал ничего, и папка подставлялась при каждом старте
    без возможности её сбросить.
    """
    input_dir = tmp_path / "audio"
    input_dir.mkdir()

    window.input_dir = str(input_dir)
    window.files_to_process = []

    window._clear_files_list()

    assert window.input_dir == ""
    assert window.user_settings.get_last_files_dir() is None


def test_clearing_forgets_transcript_dir_even_when_the_list_is_already_empty(tmp_path, window):
    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir()

    window.llm_transcript_dir = str(transcript_dir)
    window.transcript_files_for_llm = []

    window._clear_llm_files_list()

    assert window.llm_transcript_dir == ""
    assert window.user_settings.get_value("llm_transcript_dir", "") == ""


def test_clearing_with_nothing_remembered_stays_a_noop(window):
    window.input_dir = ""
    window.files_to_process = []
    window.llm_transcript_dir = ""
    window.transcript_files_for_llm = []

    window._clear_files_list()
    window._clear_llm_files_list()

    assert window.files_to_process == []
    assert window.transcript_files_for_llm == []
