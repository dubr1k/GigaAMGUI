"""What a live session has transcribed: drafts on screen and journaled finals."""

from __future__ import annotations

from datetime import datetime, timezone

from .diarization import LIVE_ESTIMATE_STABILIZATION_HORIZON_SECONDS
from .journal import EventJournal
from .types import CaptureSource, TranscriptEvent


class TranscriptState:
    """The newest draft per source and every final, journaled as it lands.

    Callers serialize access (the session lock): drafts and finals arrive
    from one ASR thread per source.
    """

    def __init__(self, journal: EventJournal, *, asr_sample_rate: int) -> None:
        self.journal = journal
        self._asr_sample_rate = asr_sample_rate
        self._partials: dict[CaptureSource, TranscriptEvent] = {}
        self._finalized_revisions: dict[tuple[CaptureSource, str], int] = {}

    def accept_partial(self, event: TranscriptEvent) -> bool:
        """Keep a draft unless its event is already final."""
        # A final is terminal for its event: a draft of it that arrives
        # later — whatever its revision — would show the finished phrase
        # again as text still being spoken.
        if (event.source, event.event_id) in self._finalized_revisions:
            return False
        self._partials[event.source] = event
        return True

    def record_final(self, event: TranscriptEvent) -> None:
        self._partials.pop(event.source, None)
        self.record_revision(event)

    def record_revision(self, event: TranscriptEvent) -> None:
        """Journal a final or a newer revision of one (a speaker label)."""
        key = (event.source, event.event_id)
        self._finalized_revisions[key] = max(event.revision, self._finalized_revisions.get(key, -1))
        self.journal.append(event)

    def events_of(self, source: CaptureSource) -> list[TranscriptEvent]:
        return [event for event in self.journal.latest_events() if event.source is source]

    def recent(self, event: TranscriptEvent) -> list[TranscriptEvent]:
        """Events of the same source within the live-estimate stabilization horizon."""
        horizon_samples = LIVE_ESTIMATE_STABILIZATION_HORIZON_SECONDS * self._asr_sample_rate
        return [
            item
            for item in self.events_of(event.source)
            if event.sample_end - item.sample_end <= horizon_samples
        ]

    def context(self) -> str:
        """The transcript as the assistant sees it: finals, then current drafts."""
        final_text = "\n".join(
            f"[{datetime.fromtimestamp(event.timestamp_ns / 1_000_000_000, timezone.utc).isoformat()}] "
            f"{event.source_label}{f' / {event.speaker}' if event.speaker else ''}: {event.text}"
            for event in self.journal.latest_events()
            if event.status == "final"
        )
        drafts = "\n".join(
            f"[{source.value.upper()} draft] {event.text}"
            for source, event in self._partials.items()
        )
        if not drafts:
            return final_text
        return f"Final transcript:\n{final_text}\n\nDraft transcript:\n{drafts}"
