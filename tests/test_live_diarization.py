import numpy as np

from src.core.diarization.base import SpeakerSegment
from src.live.session import LiveSession
from src.live.types import CaptureEvent, CaptureSource, DiarizationMode, LiveSettings, PcmChunk, TranscriptEvent


class FakeAdapter:
    def start(self, on_chunk, on_event):
        self.on_chunk = on_chunk

    def pause(self):
        return None

    def resume(self):
        return None

    def stop(self):
        return None


class NullScheduler:
    def submit(self, chunk):
        return None

    def flush(self):
        return None

    def close(self, timeout=None):
        return True


def final(event_id="mic-0", start=0, end=16_000):
    return TranscriptEvent(event_id, 0, CaptureSource.MIC, start, end, 1, "hello", "final")


def session_with(tmp_path, settings, **kwargs):
    return LiveSession(
        tmp_path,
        settings,
        kwargs.pop("adapters", {}),
        scheduler_factory=lambda source, on_final, on_partial, on_error: NullScheduler(),
        **kwargs,
    )


def test_off_mode_journals_finals_with_their_source_label_and_no_speaker(tmp_path):
    session = session_with(tmp_path, LiveSettings(diarization_mode=DiarizationMode.OFF, record_mix_audio=False))

    session._on_final(final())

    journaled = session._journal.latest_events()
    assert [(event.source_label, event.speaker, event.revision) for event in journaled] == [("MIC", None, 0)]


def test_live_estimate_does_not_load_a_model_no_backend_can_estimate_with(tmp_path, monkeypatch):
    """Every session loaded a Sortformer model per source on the ASR thread,
    only to find it has no estimate_events and report "unavailable"."""
    created = []
    monkeypatch.setattr(
        "src.core.diarization.factory.create_diarization_backend",
        lambda backend, **kwargs: created.append(backend) or object(),
    )
    updates = []
    session = session_with(tmp_path, LiveSettings(diarization_mode=DiarizationMode.LIVE_ESTIMATE, record_mix_audio=False))
    session.subscribe(updates.append)

    session._on_final(final())

    assert created == []
    assert any(isinstance(update, CaptureEvent) and "After stop" in update.detail for update in updates)
    assert session._journal.latest_events()[0].speaker is None


def test_a_factory_that_declares_no_live_estimate_is_not_asked_for_one(tmp_path):
    class Factory:
        supports_live_estimate = False

        def __init__(self):
            self.requested = []

        def __call__(self, backend):
            self.requested.append(backend)
            return object()

    factory = Factory()
    session = session_with(
        tmp_path,
        LiveSettings(diarization_mode=DiarizationMode.LIVE_ESTIMATE, record_mix_audio=False),
        diarization_factory=factory,
    )

    session._on_final(final())

    assert factory.requested == []


def test_after_stop_uses_the_backend_the_session_was_configured_with(tmp_path):
    requested = []

    class Diarizer:
        def diarize(self, path):
            return [SpeakerSegment(0.0, 1.0, "A")]

    adapter = FakeAdapter()
    session = session_with(
        tmp_path,
        LiveSettings(
            diarization_mode=DiarizationMode.AFTER_STOP,
            diarization_backend="pyannote",
            record_mix_audio=False,
        ),
        adapters={CaptureSource.MIC: adapter},
        diarization_factory=lambda backend: requested.append(backend) or Diarizer(),
    )
    session.start()
    adapter.on_chunk(PcmChunk(CaptureSource.MIC, 48_000, 1, 0, np.ones((4_800, 1), np.float32), 1))
    session._on_final(final())

    session.stop()

    assert requested == ["pyannote"]
