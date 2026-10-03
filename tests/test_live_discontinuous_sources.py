"""Sources that deliver nothing while silent (WASAPI loopback) on the session timeline.

The #50 contract stays: while audio flows, sample offsets — not arrival
times — position it. A loopback endpoint sends no packets while nothing
plays, though, so its offsets stop while time goes on; these tests drive a
session clock and capture timestamps the way such a source does.
"""

import time

import numpy as np
import soundfile as sf

from src.core.diarization.base import SpeakerSegment
from src.live.asr import LiveAsrScheduler
from src.live.session import LiveSession
from src.live.timeline import SourceTimeline
from src.live.types import (
    CaptureEvent,
    CaptureEventKind,
    CaptureSource,
    DiarizationMode,
    LiveSettings,
    PcmChunk,
)

RATE = 48_000
STEP = 480  # 10 ms


class FakeAdapter:
    def start(self, on_chunk, on_event):
        self.on_chunk = on_chunk
        self.on_event = on_event

    def pause(self):
        return None

    def resume(self):
        return None

    def stop(self):
        return None


class CollectingScheduler:
    def __init__(self):
        self.submitted = []

    def submit(self, chunk):
        self.submitted.append(chunk)

    def flush(self):
        return None

    def close(self, timeout=None):
        return True


class CollectingRecorder:
    def __init__(self, *args):
        self.mixes = []
        self.written = {CaptureSource.MIC: 0, CaptureSource.SYSTEM: 0}

    def write(self, chunk):
        self.written[chunk.source] += len(chunk.frames)

    def write_mix(self, chunk):
        self.mixes.append(chunk)

    def close(self):
        return {}


class Rig:
    def __init__(self, tmp_path, **settings):
        self.now = 0.0
        self.recorder = CollectingRecorder()
        self.adapters = {CaptureSource.MIC: FakeAdapter(), CaptureSource.SYSTEM: FakeAdapter()}
        self.schedulers = {}
        self.events = []
        self.offsets = {CaptureSource.MIC: 0, CaptureSource.SYSTEM: 0}

        def factory(source, on_final, on_partial, on_error):
            self.schedulers[source] = CollectingScheduler()
            return self.schedulers[source]

        self.session = LiveSession(
            tmp_path,
            LiveSettings(**{"record_mix_audio": True, **settings}),
            self.adapters,
            scheduler_factory=factory,
            recorder_factory=lambda *args: self.recorder,
            clock=lambda: self.now,
        )
        self.session.subscribe(self.events.append)
        self.session.start()

    def deliver(self, source, level, *, timestamp_s=None):
        """One 10 ms chunk at the source's next offset, stamped with arrival time."""
        stamp = self.now if timestamp_s is None else timestamp_s
        frames = np.full((STEP, 1), level, dtype=np.float32)
        chunk = PcmChunk(source, RATE, 1, self.offsets[source], frames, round(stamp * 1_000_000_000))
        self.offsets[source] += STEP
        self.adapters[source].on_chunk(chunk)

    def run(self, seconds, *, system=True, mic=True):
        for _ in range(round(seconds * 100)):
            self.now = round(self.now + 0.01, 6)
            if mic:
                self.deliver(CaptureSource.MIC, 0.1)
            if system:
                self.deliver(CaptureSource.SYSTEM, 0.3)

    def mix(self):
        return np.concatenate([chunk.frames[:, 0] for chunk in self.recorder.mixes])

    def mix_disabled(self):
        return [
            event for event in self.events
            if isinstance(event, CaptureEvent) and "Mixed audio recording disabled" in event.detail
        ]


def first_asr_offset_at_or_after(chunks, minimum_level=0.0):
    return [chunk.sample_offset for chunk in chunks if float(np.max(chunk.frames)) > minimum_level]


def test_an_idle_loopback_does_not_disable_the_mix(tmp_path):
    """Three idle seconds put the loopback 3 s behind its peer by offsets,
    the skew check turned mix recording off for the rest of the session."""
    rig = Rig(tmp_path)
    rig.run(1.0)
    rig.run(3.0, system=False)
    rig.run(1.0)
    rig.session.stop()

    assert rig.mix_disabled() == []
    mix = rig.mix()
    assert abs(len(mix) - 5 * RATE) <= STEP
    assert abs(mix[int(0.5 * RATE)] - 0.4) < 1e-6
    assert abs(mix[int(2.5 * RATE)] - 0.1) < 1e-6
    assert abs(mix[int(4.5 * RATE)] - 0.4) < 1e-6


def test_recognition_sees_loopback_audio_where_it_happened(tmp_path):
    """ASR offsets were source-local, so everything after an idle stretch was
    exported that much too early and sorted before speech it followed."""
    rig = Rig(tmp_path)
    rig.run(1.0)
    rig.run(3.0, system=False)
    rig.run(1.0)

    system = rig.schedulers[CaptureSource.SYSTEM].submitted
    resumed = [chunk for chunk in system if chunk.sample_offset >= 16_000]
    assert abs(resumed[0].sample_offset - 4.0 * 16_000) <= 16_000 * 0.02
    discontinuities = [
        event for event in rig.events
        if isinstance(event, CaptureEvent) and event.kind is CaptureEventKind.DISCONTINUITY
    ]
    assert len(discontinuities) == 1
    assert "idle" in discontinuities[0].detail


def test_a_loopback_that_starts_late_is_placed_late(tmp_path):
    """A first chunk more than 5 s after the session origin was pinned to the
    origin, so the mix saw a skew of the whole start delay and turned off."""
    rig = Rig(tmp_path)
    rig.run(8.0, system=False)
    rig.run(1.0)
    rig.session.stop()

    assert rig.mix_disabled() == []
    mix = rig.mix()
    assert abs(mix[int(4.0 * RATE)] - 0.1) < 1e-6
    assert abs(mix[int(8.5 * RATE)] - 0.4) < 1e-6
    system = rig.schedulers[CaptureSource.SYSTEM].submitted
    assert abs(system[0].sample_offset - 8.0 * 16_000) <= 16_000 * 0.02


def test_arrival_jitter_on_a_continuous_source_moves_nothing(tmp_path):
    """#50: jitter must neither stretch the mix nor be mistaken for idle time."""
    rig = Rig(tmp_path)
    rng = np.random.default_rng(1)
    for _ in range(1_000):
        rig.now = round(rig.now + 0.01, 6)
        rig.deliver(CaptureSource.MIC, 0.1, timestamp_s=rig.now + rng.uniform(0, 0.2))
        rig.deliver(CaptureSource.SYSTEM, 0.3, timestamp_s=rig.now + rng.uniform(0, 0.2))
    rig.session.stop()

    assert rig.mix_disabled() == []
    # The start delay between the sources is read once from jittered stamps
    # (up to 0.2 s here); beyond that the track is exactly the audio.
    assert 10 * RATE - STEP <= len(rig.mix()) <= 10.2 * RATE + STEP
    assert not [event for event in rig.events if isinstance(event, CaptureEvent) and "idle" in event.detail]
    mic = rig.schedulers[CaptureSource.MIC].submitted
    assert [chunk.sample_offset for chunk in mic[1:]] == [c.sample_offset + len(c.frames) for c in mic[:-1]]


def test_paused_time_is_not_taken_for_an_idle_gap(tmp_path):
    rig = Rig(tmp_path)
    rig.run(1.0)
    rig.session.pause()
    rig.now += 3.0
    rig.session.resume()
    rig.run(1.0)
    rig.session.stop()

    assert rig.mix_disabled() == []
    assert abs(len(rig.mix()) - 2 * RATE) <= STEP
    assert not [event for event in rig.events if isinstance(event, CaptureEvent) and "idle" in event.detail]


def test_timeline_ignores_a_clock_that_jumps_by_an_implausible_amount():
    """Liquid stamps a flushed chunk with wall time instead of host time; a jump
    of decades is a different clock, not decades of silence."""
    events = []
    timeline = SourceTimeline(CaptureSource.SYSTEM, RATE, 1, events.append)
    frames = np.zeros((STEP, 1), np.float32)

    timeline.ingest(PcmChunk(CaptureSource.SYSTEM, RATE, 1, 0, frames, 100_000_000_000))
    jumped = timeline.ingest(PcmChunk(CaptureSource.SYSTEM, RATE, 1, STEP, frames, 1_700_000_000_000_000_000))
    back = timeline.ingest(PcmChunk(CaptureSource.SYSTEM, RATE, 1, 2 * STEP, frames, 100_020_000_000))

    assert [chunk.sample_offset for chunk in jumped + back] == [STEP, 2 * STEP]
    assert events == []


def test_an_offset_jump_ends_the_open_run():
    class Backend:
        def transcribe_window(self, audio, sample_rate, offset_samples):
            return [{"transcription": "words here", "boundaries": (0, 1)}]

    finals = []
    scheduler = LiveAsrScheduler(Backend(), on_final=finals.append)
    try:
        scheduler.submit(PcmChunk(CaptureSource.SYSTEM, 16_000, 1, 0, np.ones((16_000, 1), np.float32), 1))
        scheduler.submit(PcmChunk(CaptureSource.SYSTEM, 16_000, 1, 64_000, np.ones((16_000, 1), np.float32), 2))
        deadline = time.monotonic() + 2
        while not finals and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        scheduler.close()

    assert (finals[0].sample_start, finals[0].sample_end) == (0, 16_000)
    assert finals[0].paragraph_break_after is True
    assert finals[1].sample_start == 64_000


def test_a_run_that_stops_receiving_audio_is_finalized_by_the_clock():
    """A loopback goes quiet by sending nothing, so the 3 s of silence that
    ends a run never arrived and the last phrase stayed a draft until the
    next sound, minutes later."""
    class Backend:
        def transcribe_window(self, audio, sample_rate, offset_samples):
            return [{"transcription": "words here", "boundaries": (0, 1)}]

    finals = []
    scheduler = LiveAsrScheduler(Backend(), on_final=finals.append, idle_final_seconds=0.2)
    try:
        scheduler.submit(PcmChunk(CaptureSource.SYSTEM, 16_000, 1, 0, np.ones((16_000, 1), np.float32), 1))
        deadline = time.monotonic() + 2
        while not finals and time.monotonic() < deadline:
            time.sleep(0.01)
        assert [event.sample_end for event in finals] == [16_000]
    finally:
        scheduler.close()


def test_after_stop_labels_follow_the_recording_through_idle_gaps(tmp_path):
    """system.flac holds only delivered audio; events live on the session
    timeline, so the diarizer's file times are mapped back through the gaps."""
    heard = []

    class Diarizer:
        def diarize(self, path):
            heard.append(sf.info(path).frames)
            return [SpeakerSegment(0.0, 1.0, "A"), SpeakerSegment(1.0, 2.0, "B")]

    now = [0.0]
    adapter = FakeAdapter()
    session = LiveSession(
        tmp_path,
        LiveSettings(diarization_mode=DiarizationMode.AFTER_STOP, record_mix_audio=False),
        {CaptureSource.SYSTEM: adapter},
        scheduler_factory=lambda source, on_final, on_partial, on_error: CollectingScheduler(),
        diarization_factory=lambda backend: Diarizer(),
        clock=lambda: now[0],
    )
    session.start()
    offset = 0
    for step in range(500):
        now[0] = round(step * 0.01, 6)
        if step < 100 or step >= 400:
            frames = np.full((STEP, 1), 0.2, np.float32)
            adapter.on_chunk(PcmChunk(CaptureSource.SYSTEM, RATE, 1, offset, frames, round(now[0] * 1e9)))
            offset += STEP
    from src.live.types import TranscriptEvent

    before = TranscriptEvent("system-a", 0, CaptureSource.SYSTEM, 3_200, 12_800, 1, "before", "final")
    after = TranscriptEvent("system-b", 0, CaptureSource.SYSTEM, 67_200, 76_800, 1, "after", "final")
    session._on_final(before)
    session._on_final(after)
    session.stop()

    assert heard == [2 * RATE]
    speakers = {event.event_id: event.speaker for event in session._journal.latest_events()}
    assert speakers["system-a"] != speakers["system-b"]
    assert speakers["system-b"] is not None
