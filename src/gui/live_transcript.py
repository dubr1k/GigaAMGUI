"""Presentation-only segmentation for live transcript views."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..live.types import TranscriptEvent

_SENTENCE = re.compile(r".+?(?:[.!?]+|…+)(?:[\"'»”)\]}]+)?(?=\s|$)", re.DOTALL)


@dataclass
class LiveParagraph:
    sample_start: int
    source_label: str
    speaker: str | None
    sentences: list[str] = field(default_factory=list)


@dataclass
class _StreamState:
    visible_words: list[str]
    latest_words: list[str]
    rendered: bool = False


class LiveTranscriptPresenter:
    """Group finalized speech without influencing capture or ASR decisions."""

    def __init__(self, *, long_gap_samples: int = 48_000, max_sentences: int = 3) -> None:
        self._long_gap_samples = long_gap_samples
        self._max_sentences = max_sentences
        self.paragraphs: list[LiveParagraph] = []
        self.active_text = ""
        self._active_event: TranscriptEvent | None = None
        self._last_sample_end: int | None = None
        self._force_new_paragraph = False
        self.messages: list[TranscriptEvent] = []
        self._streams: dict[str, _StreamState] = {}

    def clear(self) -> None:
        self.paragraphs.clear()
        self.active_text = ""
        self._active_event = None
        self._last_sample_end = None
        self._force_new_paragraph = False
        self.messages.clear()
        self._streams.clear()

    def add_event(self, event: TranscriptEvent) -> str:
        """Return only words stable enough to append to the active stream."""
        incoming = event.text.split()
        if not incoming:
            return ""
        key = self._stream_key(event)
        stream = self._streams.get(key)
        if stream is None:
            stream = _StreamState([], incoming)
            self._streams[key] = stream
            delta = incoming
        elif event.status == "final":
            if self._has_prefix(stream.visible_words, incoming):
                delta = incoming[len(stream.visible_words):]
            else:
                # A provisional tail was corrected; never lose the final transcript.
                stream.visible_words = []
                delta = incoming
            stream.latest_words = incoming
        else:
            common = self._common_prefix_length(stream.latest_words, incoming)
            stable_end = min(common, max(0, len(stream.latest_words) - 2))
            stable_words = stream.latest_words[:stable_end]
            delta = stable_words[len(stream.visible_words):] if self._has_prefix(stream.visible_words, stable_words) else []
            stream.latest_words = incoming
        stream.visible_words.extend(delta)
        if stream.visible_words:
            rendered_event = TranscriptEvent(
                event.event_id,
                event.revision,
                event.source,
                event.sample_start,
                event.sample_end,
                event.timestamp_ns,
                " ".join(stream.visible_words),
                event.status,
                event.speaker,
                event.supersedes,
                event.paragraph_break_after,
            )
            self.messages = [
                item for item in self.messages
                if self._stream_key(item) != key
            ]
            self.messages.append(rendered_event)
        return " ".join(delta)

    def rendered_delta(self, event: TranscriptEvent, delta: str) -> str:
        stream = self._streams[self._stream_key(event)]
        if stream.rendered:
            return delta
        stream.rendered = True
        metadata = " · ".join(filter(None, (event.source_label, event.speaker)))
        return f"{metadata}: {delta}"

    def rendered_event(self, event: TranscriptEvent) -> str:
        seconds = event.timestamp_ns / 1_000_000_000
        minutes, seconds = divmod(seconds, 60)
        timestamp = f"[{int(minutes):02d}:{seconds:06.3f}]"
        metadata = " · ".join(filter(None, (event.source_label, event.speaker)))
        return f"{timestamp} {metadata}: {event.text.strip()}"

    def rendered_messages(self) -> str:
        return "\n\n".join(self.rendered_event(event) for event in self.messages)

    @staticmethod
    def _common_prefix_length(left: list[str], right: list[str]) -> int:
        length = 0
        for previous, current in zip(left, right, strict=False):
            if previous.casefold() != current.casefold():
                break
            length += 1
        return length

    @staticmethod
    def _has_prefix(prefix: list[str], words: list[str]) -> bool:
        return len(prefix) <= len(words) and all(
            left.casefold() == right.casefold() for left, right in zip(prefix, words, strict=False)
        )

    @staticmethod
    def _stream_key(event: TranscriptEvent) -> str:
        return f"{event.source.value}:{event.event_id}"

    def add_final(self, event: TranscriptEvent) -> bool:
        """Add final ASR text and return whether it completed a sentence."""
        if event.status != "final":
            return False
        previous_sample_end = self._last_sample_end
        if self._active_event is not None and (
            self._active_event.source_label,
            self._active_event.speaker,
        ) != (event.source_label, event.speaker):
            self.paragraphs.append(LiveParagraph(
                self._active_event.sample_start,
                self._active_event.source_label,
                self._active_event.speaker,
                [self.active_text],
            ))
            self.active_text = ""
            self._active_event = None
        if self._active_event is None:
            self._active_event = event
        self.active_text = " ".join(filter(None, (self.active_text, event.text.strip())))
        completed = False
        while match := _SENTENCE.match(self.active_text):
            sentence = match.group().strip()
            self.active_text = self.active_text[match.end():].lstrip()
            self._append_sentence(
                sentence,
                self._active_event,
                previous_sample_end if not completed else event.sample_start,
            )
            completed = True
            self._active_event = event if self.active_text else None
        if event.paragraph_break_after:
            if self.active_text and self._active_event is not None:
                self.paragraphs.append(LiveParagraph(
                    self._active_event.sample_start,
                    self._active_event.source_label,
                    self._active_event.speaker,
                    [self.active_text],
                ))
                self.active_text = ""
                self._active_event = None
            self._force_new_paragraph = True
        self._last_sample_end = event.sample_end
        return completed

    def _append_sentence(
        self,
        sentence: str,
        event: TranscriptEvent,
        previous_sample_end: int | None,
    ) -> None:
        paragraph = self.paragraphs[-1] if self.paragraphs else None
        if (
            paragraph is None
            or self._force_new_paragraph
            or self._starts_new_paragraph(paragraph, event, previous_sample_end)
        ):
            paragraph = LiveParagraph(event.sample_start, event.source_label, event.speaker)
            self.paragraphs.append(paragraph)
        self._force_new_paragraph = False
        paragraph.sentences.append(sentence)

    def _starts_new_paragraph(
        self,
        paragraph: LiveParagraph,
        event: TranscriptEvent,
        previous_sample_end: int | None,
    ) -> bool:
        if (paragraph.source_label, paragraph.speaker) != (event.source_label, event.speaker):
            return True
        if len(paragraph.sentences) >= self._max_sentences:
            return True
        return (
            previous_sample_end is not None
            and event.sample_start - previous_sample_end > self._long_gap_samples
        )

    def rendered_paragraphs(self) -> str:
        rendered = [self._render_paragraph(paragraph) for paragraph in self.paragraphs]
        if self.active_text and self._active_event is not None:
            active = LiveParagraph(
                self._active_event.sample_start,
                self._active_event.source_label,
                self._active_event.speaker,
                [self.active_text],
            )
            if self.paragraphs and (
                self.paragraphs[-1].source_label,
                self.paragraphs[-1].speaker,
            ) == (active.source_label, active.speaker):
                rendered[-1] = f"{rendered[-1]} {self.active_text}"
            else:
                rendered.append(self._render_paragraph(active))
        return "\n\n".join(rendered)

    @staticmethod
    def _render_paragraph(paragraph: LiveParagraph) -> str:
        seconds = paragraph.sample_start / 16_000
        minutes, seconds = divmod(seconds, 60)
        timestamp = f"[{int(minutes):02d}:{seconds:06.3f}]"
        metadata = " · ".join(filter(None, (paragraph.source_label, paragraph.speaker)))
        return f"{timestamp} {metadata}: {' '.join(paragraph.sentences)}"
