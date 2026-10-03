"""Страница результата обработки и передача транскриптов на вкладку LLM."""

import os
import sys
import types

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
