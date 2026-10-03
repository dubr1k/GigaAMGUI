"""Materialize latest finalized transcript revisions as atomic exports."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from src.core.formatters import generate_markdown, generate_srt, generate_vtt
from src.core.subtitles import SubtitleOptions
from src.utils.atomic_json import save_text_atomic
from src.utils.time_formatter import TimeFormatter

from .types import TranscriptEvent


@dataclass(frozen=True)
class ExportSelection:
    txt: bool = False
    txt_timecodes: bool = False
    txt_diarize: bool = False
    txt_diarize_timecodes: bool = False
    md: bool = False
    srt: bool = False
    vtt: bool = False
    sentence_split: bool = True
    max_line_count: int = 2
    max_line_width: int = 64
    sample_rate: int = 48_000


def export_session(
    session_dir: Path,
    events: Iterable[TranscriptEvent],
    selection: ExportSelection,
) -> list[Path]:
    session_dir = Path(session_dir)
    finalized = _latest_final_events(events)
    exports: list[tuple[Path, str]] = []
    if selection.txt:
        exports.append((session_dir / "transcript.txt", _format_txt(finalized)))
    if selection.txt_timecodes:
        exports.append((session_dir / "transcript_timecodes.txt", _format_timecodes(finalized, selection.sample_rate)))
    if selection.txt_diarize:
        exports.append((session_dir / "transcript_diarize.txt", _format_diarized(finalized)))
    if selection.txt_diarize_timecodes:
        exports.append((session_dir / "transcript_diarize_timecodes.txt", _format_diarized_timecodes(finalized, selection.sample_rate)))
    utterances = _utterances(finalized, selection.sample_rate)
    subtitle_options = SubtitleOptions(
        sentence_split=selection.sentence_split,
        max_line_count=selection.max_line_count,
        max_line_width=selection.max_line_width,
    )
    if selection.md:
        exports.append((session_dir / "transcript.md", generate_markdown(utterances, "Live transcript", TimeFormatter())))
    if selection.srt:
        exports.append((session_dir / "transcript.srt", generate_srt(utterances, subtitle_options)))
    if selection.vtt:
        exports.append((session_dir / "transcript.vtt", generate_vtt(utterances, subtitle_options)))
    for path, content in exports:
        save_text_atomic(str(path), content)
    return [path for path, _ in exports]


def _latest_final_events(events: Iterable[TranscriptEvent]) -> list[TranscriptEvent]:
    latest: dict[str, TranscriptEvent] = {}
    for event in events:
        prior = latest.get(event.event_id)
        if prior is None or event.revision >= prior.revision:
            latest[event.event_id] = event
    return sorted(
        (event for event in latest.values() if event.status == "final"),
        key=lambda event: (event.sample_start, event.event_id),
    )


def _format_txt(events: Iterable[TranscriptEvent]) -> str:
    return "".join(f"{event.text}\n" for event in events)


def _format_timecodes(events: Iterable[TranscriptEvent], sample_rate: int) -> str:
    return "".join(f"[{_short_timestamp(event.sample_start / sample_rate)}] {event.text}\n" for event in events)


def _format_diarized(events: Iterable[TranscriptEvent]) -> str:
    return "".join(f"{_speaker_label(event)}: {event.text}\n" for event in events)


def _format_diarized_timecodes(events: Iterable[TranscriptEvent], sample_rate: int) -> str:
    return "".join(
        f"[{_short_timestamp(event.sample_start / sample_rate)}] {_speaker_label(event)}: {event.text}\n"
        for event in events
    )


def _speaker_label(event: TranscriptEvent) -> str:
    """The speaker when diarization named one, otherwise the source.

    Events without a speaker used to be left out, so a session whose
    diarization was unavailable wrote an empty diarized transcript while the
    status promised it was "retaining source labels".
    """
    return event.speaker or event.source_label


def _short_timestamp(seconds: float) -> str:
    minutes, seconds = divmod(seconds, 60)
    return f"{int(minutes):02d}:{seconds:06.3f}"


def _utterances(events: Iterable[TranscriptEvent], sample_rate: int) -> list[dict]:
    return [
        {
            "transcription": f"{event.source_label}: {event.text}",
            "boundaries": (event.sample_start / sample_rate, event.sample_end / sample_rate),
            "speaker": event.speaker,
        }
        for event in events
    ]
