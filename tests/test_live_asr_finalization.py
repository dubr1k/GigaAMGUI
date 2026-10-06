"""How runs end: finals supersede drafts, and no run outlives a bounded length."""

import threading
import time

import numpy as np

from src.live.asr import MAX_UTTERANCE_SECONDS, LiveAsrScheduler
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


def submit_in_chunks(scheduler, audio, offset=0, size=1_600):
    for start in range(0, len(audio), size):
        scheduler.submit(chunk(offset + start, audio[start:start + size]))


def syllables(seconds, level=0.2, gap_level=0.001):
    """Continuous speech: 60 ms syllables and 40 ms near-silent gaps, no pause long enough to end a run."""
    audio = np.full(int(seconds * RATE), gap_level, dtype=np.float32)
    for start in range(0, len(audio), 1_600):
        audio[start:start + 960] = level
    return audio


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


def test_continuous_speech_is_finalized_in_bounded_utterances():
    """Speech with no 3 s pause kept one run open for the whole session: hundreds
    of partials, no final, and at stop one window holding the entire session."""
    backend = FakeBackend()
    finals = []
    scheduler = LiveAsrScheduler(backend, on_final=finals.append)
    try:
        submit_in_chunks(scheduler, syllables(60))
        wait_until(lambda: len(finals) >= 2)
        limit = MAX_UTTERANCE_SECONDS * RATE
        assert all(event.sample_end - event.sample_start <= limit for event in finals)
        assert all(not event.paragraph_break_after for event in finals)
        assert [event.sample_start for event in finals[1:]] == [event.sample_end for event in finals[:-1]]
    finally:
        scheduler.close()
    assert max(length for length, _offset in backend.requests) <= MAX_UTTERANCE_SECONDS * RATE


def test_forced_cut_lands_on_the_quietest_frame_near_the_limit():
    backend = FakeBackend()
    finals = []
    scheduler = LiveAsrScheduler(backend, on_final=finals.append)
    audio = syllables(MAX_UTTERANCE_SECONDS + 2)
    dip = int((MAX_UTTERANCE_SECONDS - 2) * RATE)
    audio[dip:dip + 640] = 0.0
    try:
        submit_in_chunks(scheduler, audio)
        wait_until(lambda: finals)
    finally:
        scheduler.close()

    assert dip <= finals[0].sample_end <= dip + 640


def test_steady_loud_noise_eventually_closes_the_gate():
    """The floor adapted only while the gate was closed, so a fan or music above
    the release threshold held the gate open for the whole session."""
    finals = []
    scheduler = LiveAsrScheduler(FakeBackend(), on_final=finals.append)
    try:
        submit_in_chunks(scheduler, np.full(int(40 * RATE), 0.05, dtype=np.float32))
        wait_until(lambda: finals)
        assert len(finals) == 1
        assert finals[0].paragraph_break_after is True
        assert finals[0].sample_end < MAX_UTTERANCE_SECONDS * RATE
    finally:
        scheduler.close()


def test_speech_with_pauses_is_not_learned_as_the_noise_floor():
    finals = []
    partials = []
    scheduler = LiveAsrScheduler(FakeBackend(), on_final=finals.append, on_partial=partials.append)
    try:
        submit_in_chunks(scheduler, syllables(70, level=0.05))
        wait_until(lambda: len(finals) >= 2)
        scheduler.flush()
        wait_until(lambda: len(finals) >= 3)
    finally:
        scheduler.close()

    assert finals[-1].sample_end == 70 * RATE
    assert all(not event.paragraph_break_after for event in finals)
