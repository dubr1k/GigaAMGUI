"""How runs end: finals supersede drafts, and no run outlives a bounded length."""

import threading
import time

import numpy as np

from src.live.asr import LiveAsrScheduler
from src.live.session import LiveSession
from src.live.types import CaptureSource, LiveSettings, PcmChunk, TranscriptEvent

RATE = 16_000


class FakeBackend:
    def __init__(self):
        self.requests = []

    def transcribe_window(self, audio, sample_rate, offset_samples):
        self.requests.append((len(audio), offset_samples))
        return [{"transcription": "recognized speech", "boundaries": (0, 1)}]


def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("scheduler did not finish work")


def chunk(offset, frames):
    return PcmChunk(CaptureSource.MIC, RATE, 1, offset, np.asarray(frames, np.float32).reshape(-1, 1).copy(), 1)


def voiced(offset, seconds):
    return chunk(offset, np.ones(int(seconds * RATE)))


def silent(offset, seconds):
    return chunk(offset, np.zeros(int(seconds * RATE)))


def test_pending_partial_is_dropped_when_its_run_is_finalized():
    """A draft queued behind the final was published after it as a newer revision,
    and Liquid showed it as a second copy of the finished phrase."""
    release = threading.Event()

    class GatedBackend(FakeBackend):
        def transcribe_window(self, audio, sample_rate, offset_samples):
            if not self.requests:
                self.requests.append((len(audio), offset_samples))
                release.wait(5)
                return [{"transcription": "recognized speech", "boundaries": (0, 1)}]
            return super().transcribe_window(audio, sample_rate, offset_samples)

    backend = GatedBackend()
    events = []
    scheduler = LiveAsrScheduler(
        backend, partial_delay_seconds=0.1, on_partial=events.append, on_final=events.append,
    )
    try:
        scheduler.submit(voiced(0, 2))
        wait_until(lambda: backend.requests)
        scheduler.submit(voiced(2 * RATE, 1))
        scheduler.submit(silent(3 * RATE, 3))
        release.set()
        wait_until(lambda: any(event.status == "final" for event in events))
        time.sleep(0.1)
    finally:
        release.set()
        scheduler.close()

    statuses = [event.status for event in events]
    assert "partial" not in statuses[statuses.index("final"):]


def test_flush_drops_the_pending_partial_of_the_flushed_run():
    release = threading.Event()

    class GatedBackend(FakeBackend):
        def transcribe_window(self, audio, sample_rate, offset_samples):
            first = not self.requests
            super().transcribe_window(audio, sample_rate, offset_samples)
            if first:
                release.wait(5)
            return [{"transcription": "recognized speech", "boundaries": (0, 1)}]

    backend = GatedBackend()
    events = []
    scheduler = LiveAsrScheduler(
        backend, partial_delay_seconds=0.1, on_partial=events.append, on_final=events.append,
    )
    scheduler.submit(voiced(0, 2))
    wait_until(lambda: backend.requests)
    scheduler.submit(voiced(2 * RATE, 1))
    scheduler.flush()
    release.set()
    scheduler.close()

    statuses = [event.status for event in events]
    assert statuses[-1] == "final"
    assert "partial" not in statuses[statuses.index("final"):]


def test_session_rejects_any_partial_of_a_finalized_event(tmp_path):
    updates = []
    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False),
        {},
        scheduler_factory=lambda source, on_final, on_partial, on_error: None,
    )
    session.subscribe(updates.append)
    final = TranscriptEvent("mic-0", 1, CaptureSource.MIC, 0, 16_000, 1, "Final", "final")
    late = TranscriptEvent("mic-0", 2, CaptureSource.MIC, 0, 16_000, 1, "Final", "partial")

    session._on_final(final)
    session._on_partial(late)

    assert updates == [final]
