import json
from pathlib import Path

import numpy as np
import pytest

from src.core.diarization.base import SpeakerSegment
from src.live.exports import ExportSelection
from src.live.session import LiveSession
from src.live.types import (
    CaptureEvent,
    CaptureSource,
    CaptureState,
    DiarizationMode,
    LiveSettings,
    PcmChunk,
    TranscriptEvent,
)


class FakeAdapter:
    def __init__(self):
        self.paused = False
        self.stopped = False

    def start(self, on_chunk, on_event):
        self.on_chunk = on_chunk
        self.on_event = on_event

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def stop(self):
        self.stopped = True

    def emit(self, chunk):
        self.on_chunk(chunk)


class FakeScheduler:
    def __init__(self, on_final, on_partial=lambda event: None):
        self._on_final = on_final
        self._on_partial = on_partial
        self.submitted = []
        self.flushed = False
        self.closed = False

    def submit(self, chunk):
        self.submitted.append(chunk)

    def flush(self):
        self.flushed = True

    def close(self):
        self.closed = True

    def finalize(self, source, text):
        chunk = self.submitted[-1]
        self._on_final(
            TranscriptEvent(
                event_id=f"{source.value}-{chunk.sample_offset}",
                revision=0,
                source=source,
                sample_start=chunk.sample_offset,
                sample_end=chunk.sample_offset + len(chunk.frames),
                timestamp_ns=1,
                text=text,
                status="final",
            )
        )

    def partial(self, source, text):
        chunk = self.submitted[-1]
        self._on_partial(TranscriptEvent(
            event_id=f"{source.value}-{chunk.sample_offset}", revision=0,
            source=source, sample_start=chunk.sample_offset,
            sample_end=chunk.sample_offset + len(chunk.frames), timestamp_ns=1,
            text=text, status="partial",
        ))


def source_chunk(source, offset=0):
    return PcmChunk(
        source,
        48_000,
        1,
        offset,
        np.ones((4_800, 1), dtype=np.float32),
        1,
    )


def test_session_records_journals_and_submits_derived_audio(tmp_path):
    adapter = FakeAdapter()
    schedulers = {}

    def scheduler_factory(source, on_final, on_partial, on_error):
        scheduler = FakeScheduler(on_final, on_partial)
        schedulers[source] = scheduler
        return scheduler

    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False),
        {CaptureSource.MIC: adapter},
        scheduler_factory=scheduler_factory,
    )

    session.start()
    adapter.emit(source_chunk(CaptureSource.MIC))
    schedulers[CaptureSource.MIC].finalize(CaptureSource.MIC, "hello")
    result = session.stop()

    assert session.status().state is CaptureState.STOPPED
    assert len(schedulers[CaptureSource.MIC].submitted[0].frames) == 1_600
    assert schedulers[CaptureSource.MIC].submitted[0].sample_offset == 0
    assert result.recordings == {CaptureSource.MIC: result.session_dir / "mic.flac"}
    assert json.loads((result.session_dir / "events.jsonl").read_text()) ["text"] == "hello"


def test_session_notifies_partial_revisions_without_journaling_them(tmp_path):
    adapter = FakeAdapter()
    updates = []
    scheduler = None

    def scheduler_factory(source, on_final, on_partial, on_error):
        nonlocal scheduler
        scheduler = FakeScheduler(on_final, on_partial)
        return scheduler

    session = LiveSession(
        tmp_path, LiveSettings(record_mix_audio=False), {CaptureSource.MIC: adapter},
        scheduler_factory=scheduler_factory,
    )
    session.subscribe(updates.append)
    session.start()
    adapter.emit(source_chunk(CaptureSource.MIC))
    scheduler.partial(CaptureSource.MIC, "A complete thought")

    assert updates[-1].status == "partial"
    assert session._journal.latest_events() == []


def test_session_pause_stops_accepting_chunks_until_resumed(tmp_path):
    adapter = FakeAdapter()
    scheduler = FakeScheduler(lambda event: None)
    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False),
        {CaptureSource.MIC: adapter},
        scheduler_factory=lambda source, on_final, on_partial, on_error: scheduler,
    )

    session.start()
    session.pause()
    adapter.emit(source_chunk(CaptureSource.MIC))

    assert session.status().state is CaptureState.PAUSED
    assert adapter.paused is True
    assert scheduler.submitted == []


def test_session_resume_restarts_capture_and_accepts_chunks(tmp_path):
    adapter = FakeAdapter()
    scheduler = FakeScheduler(lambda event: None)
    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False),
        {CaptureSource.MIC: adapter},
        scheduler_factory=lambda source, on_final, on_partial, on_error: scheduler,
    )

    session.start()
    session.pause()
    session.resume()
    adapter.emit(source_chunk(CaptureSource.MIC))

    assert session.status().state is CaptureState.RECORDING
    assert adapter.paused is False
    assert len(scheduler.submitted) == 1


def test_session_rejects_partial_for_finalized_source_event_revision(tmp_path):
    updates = []
    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False),
        {},
        scheduler_factory=lambda source, on_final, on_partial, on_error: FakeScheduler(on_final, on_partial),
    )
    session.subscribe(updates.append)
    final = TranscriptEvent("shared", 1, CaptureSource.MIC, 0, 1, 1, "Final", "final")
    stale_partial = TranscriptEvent("shared", 1, CaptureSource.MIC, 0, 1, 1, "Stale", "partial")
    other_source_partial = TranscriptEvent("shared", 1, CaptureSource.SYSTEM, 0, 1, 1, "Other", "partial")

    session._on_final(final)
    session._on_partial(stale_partial)
    session._on_partial(other_source_partial)

    assert updates == [final, other_source_partial]


def test_session_rejects_partial_for_diarization_revised_final(tmp_path):
    updates = []

    class FakeSortformer:
        def estimate_events(self, events, stabilization_horizon_seconds):
            return {event.event_id: "speaker" for event in events}

    session = LiveSession(
        tmp_path,
        LiveSettings(diarization_mode=DiarizationMode.LIVE_ESTIMATE, record_mix_audio=False),
        {},
        scheduler_factory=lambda source, on_final, on_partial, on_error: FakeScheduler(on_final, on_partial),
        diarization_factory=lambda backend: FakeSortformer(),
    )
    session.subscribe(updates.append)
    session._on_final(TranscriptEvent("event", 0, CaptureSource.MIC, 0, 1, 1, "Final", "final"))
    session._on_partial(TranscriptEvent("event", 1, CaptureSource.MIC, 0, 1, 1, "Stale", "partial"))

    assert [event.status for event in updates] == ["final", "final"]


def test_session_preserves_stereo_source_audio_while_deriving_mono_asr(tmp_path):
    adapter = FakeAdapter()
    scheduler = FakeScheduler(lambda event: None)
    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False),
        {CaptureSource.MIC: adapter},
        scheduler_factory=lambda source, on_final, on_partial, on_error: scheduler,
    )
    stereo = PcmChunk(
        CaptureSource.MIC,
        48_000,
        2,
        0,
        np.ones((4_800, 2), dtype=np.float32),
        1,
    )

    session.start()
    adapter.emit(stereo)

    submitted = scheduler.submitted[0]
    assert submitted.sample_rate == 16_000
    assert submitted.channels == 1
    assert len(submitted.frames) == 1_600


def test_ask_context_includes_only_final_events_with_timestamp_source_and_speaker(tmp_path):
    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False),
        {},
        scheduler_factory=lambda source, on_final, on_partial, on_error: FakeScheduler(on_final, on_partial),
    )

    session._on_final(TranscriptEvent(
        "final", 0, CaptureSource.MIC, 0, 1, 1_000_000_000,
        "Final text", "final", speaker="Speaker 1",
    ))
    session._on_final(TranscriptEvent(
        "partial", 0, CaptureSource.SYSTEM, 1, 2, 2_000_000_000,
        "Partial text", "partial", speaker="Speaker 2",
    ))

    assert session.ask_context() == "[1970-01-01T00:00:01+00:00] MIC / Speaker 1: Final text"


def test_session_context_includes_latest_partial_as_a_source_labelled_draft(tmp_path):
    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False),
        {},
        scheduler_factory=lambda source, on_final, on_partial, on_error: FakeScheduler(on_final, on_partial),
    )

    session._on_final(TranscriptEvent("final", 0, CaptureSource.MIC, 0, 1, 1, "Final text", "final"))
    session._on_partial(TranscriptEvent("draft", 0, CaptureSource.SYSTEM, 1, 2, 2, "Still speaking", "partial"))

    assert session.ask_context() == (
        "Final transcript:\n[1970-01-01T00:00:00+00:00] MIC: Final text\n\n"
        "Draft transcript:\n[SYSTEM draft] Still speaking"
    )


def test_session_persists_conversation_and_freezes_it_after_stop(tmp_path):
    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False),
        {},
        scheduler_factory=lambda source, on_final, on_partial, on_error: FakeScheduler(on_final, on_partial),
    )

    turn = session.begin_conversation("What was agreed?")
    session.append_conversation_answer(turn.id, "Friday")
    session.finish_conversation(turn.id)
    session.start()
    session.stop()

    assert [(item.question, item.answer, item.status) for item in session.conversation()] == [
        ("What was agreed?", "Friday", "complete"),
    ]
    assert json.loads((session._session_dir / "conversation.jsonl").read_text()) == {
        "id": turn.id,
        "question": "What was agreed?",
        "answer": "Friday",
        "status": "complete",
    }
    with pytest.raises(RuntimeError, match="frozen"):
        session.begin_conversation("One more question")


def test_session_records_mix_when_aligned_sources_arrive(tmp_path):
    mic = FakeAdapter()
    system = FakeAdapter()
    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=True),
        {CaptureSource.MIC: mic, CaptureSource.SYSTEM: system},
        scheduler_factory=lambda source, on_final, on_partial, on_error: FakeScheduler(on_final, on_partial),
    )

    session.start()
    mic.emit(source_chunk(CaptureSource.MIC))
    system.emit(source_chunk(CaptureSource.SYSTEM))
    result = session.stop()

    assert (result.session_dir / "mix.flac").exists()
    recordings = json.loads((result.session_dir / "metadata.json").read_text())["recordings"]
    assert set(recordings) == {"mic", "system", "mix"}
    assert recordings["mix"] == {
        "paths": [str(result.session_dir / "mix.flac")],
        "codec": "FLAC PCM_24",
        "rate": 48_000,
        "channels": 1,
        "frames": 4_800,
        "bytes": 14_400,
        "segments": [{"path": str(result.session_dir / "mix.flac"), "frames": 4_800, "bytes": 14_400}],
    }


def test_session_records_only_explicitly_selected_source_tracks(tmp_path):
    mic = FakeAdapter()
    system = FakeAdapter()
    session = LiveSession(
        tmp_path,
        LiveSettings(record_mic_audio=True, record_system_audio=False, record_mix_audio=False),
        {CaptureSource.MIC: mic, CaptureSource.SYSTEM: system},
        scheduler_factory=lambda source, on_final, on_partial, on_error: FakeScheduler(on_final, on_partial),
    )

    session.start()
    mic.emit(source_chunk(CaptureSource.MIC))
    system.emit(source_chunk(CaptureSource.SYSTEM))
    result = session.stop()

    assert result.recordings == {CaptureSource.MIC: result.session_dir / "mic.flac"}
    assert not (result.session_dir / "system.flac").exists()


def test_off_mode_does_not_construct_a_diarizer(tmp_path):
    session = LiveSession(
        tmp_path,
        LiveSettings(diarization_mode=DiarizationMode.OFF, record_mix_audio=False),
        {},
        scheduler_factory=lambda source, on_final, on_partial, on_error: FakeScheduler(on_final, on_partial),
        diarization_factory=lambda backend: (_ for _ in ()).throw(AssertionError(backend)),
    )

    session.start()
    session.stop()


def test_after_stop_revises_events_and_materializes_selected_exports(tmp_path):
    adapter = FakeAdapter()

    class FakeDiarizer:
        def diarize(self, path):
            assert Path(path).name == "mic.flac"
            return [SpeakerSegment(0.0, 1.0, "model-speaker-a")]

    session = LiveSession(
        tmp_path,
        LiveSettings(diarization_mode=DiarizationMode.AFTER_STOP, record_mix_audio=False),
        {CaptureSource.MIC: adapter},
        scheduler_factory=lambda source, on_final, on_partial, on_error: FakeScheduler(on_final, on_partial),
        diarization_factory=lambda backend: FakeDiarizer(),
        export_selection=ExportSelection(txt=True, sample_rate=16_000),
    )
    session.start()
    adapter.emit(source_chunk(CaptureSource.MIC))
    session._on_final(TranscriptEvent("mic-0", 0, CaptureSource.MIC, 0, 16_000, 1, "hello", "final"))

    result = session.stop()

    events = session._journal.latest_events()
    assert [(event.revision, event.source_label, event.speaker) for event in events] == [(1, "MIC", "Speaker 1")]
    assert (result.session_dir / "transcript.txt").read_text(encoding="utf-8") == "hello\n"


def test_live_estimate_stabilizes_recent_events_and_reports_unavailable_sortformer(tmp_path):
    updates = []
    estimate_calls = []

    class FakeSortformer:
        def estimate_events(self, events, stabilization_horizon_seconds):
            assert stabilization_horizon_seconds == 10
            estimate_calls.append([event.event_id for event in events])
            return {event.event_id: "stream-speaker" for event in events}

    session = LiveSession(
        tmp_path,
        LiveSettings(diarization_mode=DiarizationMode.LIVE_ESTIMATE, record_mix_audio=False),
        {},
        scheduler_factory=lambda source, on_final, on_partial, on_error: FakeScheduler(on_final, on_partial),
        diarization_factory=lambda backend: FakeSortformer(),
    )
    session.subscribe(updates.append)
    session._on_final(TranscriptEvent("mic-0", 0, CaptureSource.MIC, 0, 16_000, 1, "hello", "final"))
    session._on_final(TranscriptEvent("mic-20", 0, CaptureSource.MIC, 320_000, 336_000, 1, "later", "final"))

    labeled = {event.event_id: event for event in session._journal.latest_events()}
    assert (labeled["mic-0"].revision, labeled["mic-0"].source_label, labeled["mic-0"].speaker) == (1, "MIC", "Speaker 1")
    assert estimate_calls == [["mic-0"], ["mic-20"]]

    unavailable = LiveSession(
        tmp_path,
        LiveSettings(diarization_mode=DiarizationMode.LIVE_ESTIMATE, record_mix_audio=False),
        {},
        scheduler_factory=lambda source, on_final, on_partial, on_error: FakeScheduler(on_final, on_partial),
        diarization_factory=lambda backend: object(),
    )
    unavailable.subscribe(updates.append)
    unavailable._on_final(TranscriptEvent("system-0", 0, CaptureSource.SYSTEM, 0, 16_000, 1, "hello", "final"))

    assert any(isinstance(update, CaptureEvent) and "Sortformer" in update.detail for update in updates)
    assert unavailable._journal.latest_events()[0].source_label == "SYSTEM"


@pytest.mark.parametrize(
    ("translate", "speaker", "detail"),
    [
        (
            lambda ru, en: ru,
            "Спикер 1",
            "Sortformer unavailable. Используйте «После остановки» для офлайн-меток "
            "спикеров; метки источников сохраняются.",
        ),
        (
            lambda ru, en: en,
            "Speaker 1",
            "Sortformer unavailable. Use After stop for offline speaker labels; "
            "retaining source labels.",
        ),
    ],
)
def test_live_session_localizes_speaker_and_diarization_messages(tmp_path, translate, speaker, detail):
    updates = []
    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False),
        {},
        scheduler_factory=lambda source, on_final, on_partial, on_error: FakeScheduler(on_final, on_partial),
        translate=translate,
    )
    session.subscribe(updates.append)

    assert session._anonymous_speaker(CaptureSource.MIC, "model-speaker") == speaker
    session._report_live_diarization_unavailable(CaptureSource.MIC, "Sortformer unavailable.")

    assert updates[-1].detail == detail
