import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from src.gui.live_overlay import LiveOverlay
from src.live.session import ConversationTurn
from src.live.types import CaptureSource, TranscriptEvent


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def overlay(qapp):
    instance = LiveOverlay()
    instance.show()
    yield instance
    instance.close()


def _event(text, status="final", source=CaptureSource.MIC, speaker=None, event_id="event-1"):
    return TranscriptEvent(
        event_id=event_id,
        revision=0,
        source=source,
        sample_start=0,
        sample_end=16000,
        timestamp_ns=0,
        text=text,
        status=status,
        speaker=speaker,
    )


def test_overlay_displays_recent_final_lines_partial_and_metadata(overlay):
    overlay.update_transcript(_event("Final one", speaker="Speaker 1"))
    overlay.update_transcript(_event("Final two", source=CaptureSource.SYSTEM))
    overlay.update_transcript(_event("Still listening", status="partial"))

    assert "Speaker 1" in overlay.final_text.toPlainText()
    assert "MIC" in overlay.final_text.toPlainText()
    assert "SYSTEM" in overlay.final_text.toPlainText()
    assert "Still listening" not in overlay.final_text.toPlainText()
    assert overlay.partial_label.text() == ""


def test_overlay_keeps_long_history_and_preserves_manual_scroll_position(overlay, qapp):
    assert overlay.final_text.verticalScrollBarPolicy() is Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    for number in range(30):
        # Distinct events: a final with the same id replaces the earlier one.
        overlay.update_transcript(_event(f"Final line {number}", event_id=f"event-{number}"))
    qapp.processEvents()
    scrollbar = overlay.final_text.verticalScrollBar()

    assert "Final line 0" in overlay.final_text.toPlainText()
    scrollbar.setValue(0)
    overlay.update_transcript(_event("Newest line", event_id="event-newest"))
    assert scrollbar.value() == 0

    scrollbar.setValue(scrollbar.maximum())
    overlay.update_transcript(_event("Following newest", event_id="event-following"))
    assert scrollbar.value() == scrollbar.maximum()


def test_overlay_collapses_hides_and_can_be_dragged(overlay):
    assert overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert overlay.windowFlags() & Qt.WindowType.FramelessWindowHint

    overlay.toggle_collapsed()
    assert overlay.content.isVisible() is False
    overlay.toggle_collapsed()
    assert overlay.content.isVisible() is True

    original = overlay.pos()
    QTest.mousePress(overlay.header, Qt.MouseButton.LeftButton, pos=overlay.header.rect().center())
    QTest.mouseMove(overlay.header, overlay.header.rect().center() + QPoint(40, 40))
    QTest.mouseRelease(overlay.header, Qt.MouseButton.LeftButton, pos=overlay.header.rect().center() + QPoint(40, 40))
    assert overlay.pos() != original

    overlay.close()
    assert overlay.isVisible() is False


def test_overlay_submits_questions_and_hides_answer_card(overlay):
    questions = []
    overlay.question_submitted.connect(questions.append)
    overlay.question_input.setText("What was agreed?")
    overlay.send_button.click()

    overlay.set_answer("The deadline is Friday.")
    assert questions == ["What was agreed?"]
    assert overlay.answer_text.toPlainText() == (
        "You: What was agreed?\nAssistant: The deadline is Friday."
    )
    assert overlay.answer_card.isVisible() is True

    overlay.toggle_answer_visibility()
    assert overlay.answer_card.isVisible() is False
    assert overlay.answer_toggle_button.text() == "Show answer"


def test_overlay_keeps_submitted_question_and_can_cancel_generation(overlay):
    cancelled = []
    overlay.cancel_requested.connect(lambda: cancelled.append(True))

    overlay.question_input.setText("What was agreed?")
    overlay.send_button.click()

    assert "What was agreed?" in overlay.answer_text.toPlainText()
    assert "Generating" in overlay.answer_text.toPlainText()
    assert overlay.cancel_button.isVisible() is True
    overlay.cancel_button.click()

    assert cancelled == [True]
    assert "What was agreed?" in overlay.answer_text.toPlainText()
    assert "Generating" not in overlay.answer_text.toPlainText()
    assert overlay.cancel_button.isVisible() is False


def test_overlay_animates_generation_and_appends_streamed_answer(overlay):
    overlay.question_input.setText("What was agreed?")
    overlay.send_button.click()
    initial = overlay.answer_text.toPlainText()

    overlay._advance_generating_ellipsis()
    animated = overlay.answer_text.toPlainText()
    overlay.append_answer("Friday")
    overlay.append_answer(" at noon.")
    overlay.finish_generation()

    assert initial != animated
    assert "Generating" not in overlay.answer_text.toPlainText()
    assert overlay.answer_text.toPlainText().endswith("Friday at noon.")
    assert overlay._generation_timer.isActive() is False


def test_overlay_renders_session_owned_conversation_history(overlay):
    overlay.set_conversation([
        ConversationTurn("one", "What was agreed?", "Friday", "complete"),
        ConversationTurn("two", "Who owns it?", "Generating...", "generating"),
    ])

    assert overlay.answer_text.toPlainText() == (
        "You: What was agreed?\nAssistant: Friday\n\n"
        "You: Who owns it?\nAssistant: Generating..."
    )
