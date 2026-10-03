"""Страница результата обработки и передача транскриптов на вкладку LLM."""

import os
import sys
import types
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

sys.modules.setdefault("gigaam", types.SimpleNamespace(load_model=lambda *args, **kwargs: object()))
sys.modules.setdefault("yt_dlp", types.SimpleNamespace(YoutubeDL=object))

from src.gui.app_qt import GigaTranscriberQtApp  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_gui_config(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGAAM_CONFIG_DIR", str(tmp_path / "config"))


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    instance = GigaTranscriberQtApp()
    instance._lang = "ru"
    yield instance
    instance.close()
    app.processEvents()


def _flush_deletes():
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    QApplication.processEvents()


def _segments(count):
    return [
        {"start": f"00:00:0{index}", "seconds": float(index), "speaker": "Спикер 1", "text": f"фраза {index}"}
        for index in range(count)
    ]


def test_repopulating_the_transcript_replaces_segment_rows(window):
    window._populate_result_transcript(_segments(5), "")
    _flush_deletes()
    window._populate_result_transcript(_segments(5), "")
    _flush_deletes()

    buttons = [b for b in window.result_transcript.findChildren(QPushButton) if b.objectName() == "text_button"]
    bodies = [label for label in window.result_transcript.findChildren(QLabel) if label.objectName() == "result_segment"]
    assert len(buttons) == 5
    assert len(bodies) == 5

    window._populate_result_transcript([], "текст без таймкодов")
    _flush_deletes()
    assert window.result_transcript.findChildren(QPushButton) == []
    assert [label.text() for label in window.result_transcript.findChildren(QLabel)] == ["текст без таймкодов"]


def test_opening_transcripts_switches_to_the_llm_tab(window, tmp_path):
    transcript = tmp_path / "meeting.txt"
    transcript.write_text("текст", encoding="utf-8")
    window.tabs.setCurrentIndex(0)
    window.transcript_files_for_llm = []

    window.open_paths_from_system([str(transcript)])

    assert window.tabs.tabText(window.tabs.currentIndex()) == "LLM"
    assert window.llm_files_list.count() == 1


def test_finished_batch_lists_its_transcripts_on_the_llm_tab(window, tmp_path, monkeypatch):
    transcript = tmp_path / "meeting.txt"
    transcript.write_text("текст", encoding="utf-8")
    monkeypatch.setattr(window, "_show_completion_dialog", lambda *a, **k: None)
    window.transcript_files_for_llm = []
    window._refresh_llm_files_list()
    window._last_generated_transcript_files = [str(transcript)]

    window._on_processing_finished(True, "Готово")

    assert window.transcript_files_for_llm == [str(transcript)]
    assert window.llm_files_list.count() == 1
    assert window.llm_files_list.isHidden() is False


def test_pasted_text_and_transcript_file_get_separate_llm_results(window, tmp_path):
    transcript = tmp_path / "meeting.txt"
    transcript.write_text("текст встречи", encoding="utf-8")
    window.transcript_files_for_llm = [str(transcript)]
    window.txt_llm_transcript.setPlainText("вставленный вручную текст")
    window.llm_output_dir = ""

    inputs = window._collect_llm_inputs()
    saved = [
        path
        for entry in inputs
        for path in window._save_llm_result(entry, f"ответ для {entry['name']}", "summary", ["txt"])
    ]

    assert len(inputs) == 2
    assert len(set(saved)) == 2, saved
    assert len({Path(path).read_text(encoding="utf-8") for path in saved}) == 2
