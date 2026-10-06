from dataclasses import replace

import pytest

from src.gui.live_transcript import LiveTranscriptPresenter
from src.live.types import CaptureSource, TranscriptEvent


def _event(
    text: str,
    *,
    event_id: str = "event",
    source: CaptureSource = CaptureSource.MIC,
    speaker: str | None = None,
    sample_start: int = 0,
    sample_end: int = 16_000,
    paragraph_break_after: bool = False,
) -> TranscriptEvent:
    return TranscriptEvent(
        event_id=event_id,
        revision=0,
        source=source,
        sample_start=sample_start,
        sample_end=sample_end,
        timestamp_ns=0,
        text=text,
        status="final",
        speaker=speaker,
        paragraph_break_after=paragraph_break_after,
    )


def test_presenter_shows_the_initial_partial_tail_without_waiting_for_a_revision():
    presenter = LiveTranscriptPresenter()

    first = _event("First part of the", event_id="one")
    first = replace(first, status="partial")
    second = _event("First part of the revised sentence", event_id="one")
    second = replace(second, status="partial")
    final = _event("First part of the revised sentence.", event_id="one")

    assert presenter.add_event(first) == "First part of the"
    assert presenter.add_event(second) == ""
    assert presenter.add_event(final) == "revised sentence."
    assert presenter.rendered_messages() == "[00:00.000] MIC: First part of the revised sentence."


def test_presenter_keeps_revisions_inside_uncommitted_tail():
    presenter = LiveTranscriptPresenter()
    first = _event("One two three four", event_id="one")
    first = replace(first, status="partial")
    second = _event("One two changed four five", event_id="one")
    second = replace(second, status="partial")
    final = _event("One two changed four five.", event_id="one")

    assert presenter.add_event(first) == "One two three four"
    assert presenter.add_event(second) == ""
    assert presenter.add_event(final) == "One two changed four five."
    assert presenter.rendered_messages() == "[00:00.000] MIC: One two changed four five."


def test_presenter_keeps_incomplete_sentence_active_until_later_completion():
    presenter = LiveTranscriptPresenter()

    presenter.add_final(_event("Unfinished words", event_id="one"))
    presenter.add_final(_event(" continue", event_id="two"))

    assert presenter.paragraphs == []
    assert presenter.active_text == "Unfinished words continue"


def test_presenter_starts_paragraphs_for_metadata_gap_and_sentence_limit():
    presenter = LiveTranscriptPresenter(long_gap_samples=16_000)

    for number in range(3):
        start = number * 16_000
        presenter.add_final(_event(f"Sentence {number}.", event_id=str(number), sample_start=start, sample_end=start + 1))
    presenter.add_final(_event("Fourth sentence.", event_id="four", sample_start=48_000, sample_end=48_001))
    presenter.add_final(_event("Other speaker.", event_id="speaker", speaker="Speaker 2", sample_start=48_001, sample_end=48_002))
    presenter.add_final(_event("After gap.", event_id="gap", speaker="Speaker 2", sample_start=64_003, sample_end=64_004))

    assert [paragraph.sentences for paragraph in presenter.paragraphs] == [
        ["Sentence 0.", "Sentence 1.", "Sentence 2."],
        ["Fourth sentence."],
        ["Other speaker."],
        ["After gap."],
    ]
    assert presenter.paragraphs[2].speaker == "Speaker 2"


def test_presenter_starts_a_new_paragraph_after_a_silence_finalized_phrase():
    presenter = LiveTranscriptPresenter()

    presenter.add_final(_event("First phrase.", event_id="one", paragraph_break_after=True))
    presenter.add_final(_event("Second phrase.", event_id="two", sample_start=16_000))

    assert [paragraph.sentences for paragraph in presenter.paragraphs] == [
        ["First phrase."],
        ["Second phrase."],
    ]



@pytest.mark.parametrize(
    ("text", "sentences"),
    [
        ("Первая мысль… Вторая мысль.", ["Первая мысль…", "Вторая мысль."]),
        ("Он сказал: «Готово!» Дальше.", ["Он сказал: «Готово!»", "Дальше."]),
    ],
)
def test_presenter_recognizes_unicode_and_quoted_sentence_endings(text, sentences):
    presenter = LiveTranscriptPresenter()

    assert presenter.add_final(_event(text)) is True
    assert presenter.active_text == ""
    assert presenter.paragraphs[0].sentences == sentences
