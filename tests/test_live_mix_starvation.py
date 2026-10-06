"""Issue #42: a silent second source must not disable mixed recording."""

import numpy as np

from src.live.session import MAX_PENDING_MIX_CHUNKS, LiveSession
from src.live.types import CaptureSource, LiveSettings, PcmChunk


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


class FakeScheduler:
    def __init__(self):
        self.submitted = []

    def submit(self, chunk):
        self.submitted.append(chunk)

    def flush(self):
        return None

    def close(self, timeout=None):
        return None


class RecordingRecorder:
    def __init__(self):
        self.mixes = []

    def write(self, chunk):
        return None

    def write_mix(self, chunk):
        self.mixes.append(chunk)

    def close(self):
        return {}


def chunk(source, offset, frames=480, rate=48_000):
    return PcmChunk(
        source, rate, 1, offset,
        np.full((frames, 1), 0.1, dtype=np.float32),
        int(offset / rate * 1_000_000_000) + 1,
    )


def build(tmp_path, recorder):
    adapters = {CaptureSource.MIC: FakeAdapter(), CaptureSource.SYSTEM: FakeAdapter()}
    schedulers = {}

    def factory(source, on_final, on_partial, on_error):
        schedulers[source] = FakeScheduler()
        return schedulers[source]

    events = []
    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=True, record_source_audio=False),
        adapters,
        scheduler_factory=factory,
        recorder_factory=lambda *args, **kwargs: recorder,
    )
    session.subscribe(events.append)
    session.start()
    return session, adapters, schedulers, events


def _mix_disabled(events):
    return [
        event for event in events
        if isinstance(getattr(event, "detail", None), str) and "Mixed audio" in event.detail
    ]


def test_silent_system_source_does_not_disable_mixing(tmp_path):
    recorder = RecordingRecorder()
    session, adapters, schedulers, events = build(tmp_path, recorder)

    for index in range(MAX_PENDING_MIX_CHUNKS * 3):
        adapters[CaptureSource.MIC].on_chunk(chunk(CaptureSource.MIC, index * 480))

    assert _mix_disabled(events) == []
    assert recorder.mixes, "microphone audio was never written to the mix track"
    assert len(schedulers[CaptureSource.MIC].submitted) == MAX_PENDING_MIX_CHUNKS * 3


def test_mixing_pairs_again_once_the_second_source_wakes_up(tmp_path):
    recorder = RecordingRecorder()
    session, adapters, schedulers, events = build(tmp_path, recorder)

    for index in range(10):
        adapters[CaptureSource.MIC].on_chunk(chunk(CaptureSource.MIC, index * 480))
    mic_only = len(recorder.mixes)
    for index in range(10):
        adapters[CaptureSource.SYSTEM].on_chunk(chunk(CaptureSource.SYSTEM, index * 480))
        adapters[CaptureSource.MIC].on_chunk(chunk(CaptureSource.MIC, (10 + index) * 480))

    assert _mix_disabled(events) == []
    assert len(recorder.mixes) > mic_only


def test_a_source_that_starts_later_is_placed_later_in_the_mix(tmp_path):
    """Both first chunks used to land on the session origin, so a system stream
    that started 0.5 s after the microphone played 0.5 s early in mix.flac."""
    recorder = RecordingRecorder()
    session, adapters, schedulers, events = build(tmp_path, recorder)

    def tone(source, offset, start_ns, amplitude):
        return PcmChunk(
            source, 48_000, 1, offset,
            np.full((480, 1), amplitude, dtype=np.float32),
            start_ns + int(offset / 48_000 * 1_000_000_000),
        )

    for index in range(100):
        adapters[CaptureSource.MIC].on_chunk(tone(CaptureSource.MIC, index * 480, 1, 0.1))
        if index >= 50:
            system_index = index - 50
            adapters[CaptureSource.SYSTEM].on_chunk(
                tone(CaptureSource.SYSTEM, system_index * 480, 500_000_001, 0.3)
            )

    mix = np.concatenate([chunk.frames[:, 0] for chunk in recorder.mixes])
    assert _mix_disabled(events) == []
    assert mix[int(0.25 * 48_000)] == np.float32(0.1)
    assert abs(mix[int(0.75 * 48_000)] - 0.4) < 1e-6


def test_mix_writer_failure_still_disables_mixing_once(tmp_path):
    class BrokenRecorder(RecordingRecorder):
        def write_mix(self, chunk):
            raise OSError("mix writer failed")

    recorder = BrokenRecorder()
    session, adapters, schedulers, events = build(tmp_path, recorder)

    for index in range(20):
        adapters[CaptureSource.MIC].on_chunk(chunk(CaptureSource.MIC, index * 480))
        adapters[CaptureSource.SYSTEM].on_chunk(chunk(CaptureSource.SYSTEM, index * 480))

    assert len(_mix_disabled(events)) == 1
