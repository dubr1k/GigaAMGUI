import numpy as np

from src.live.session import MAX_PENDING_MIX_CHUNKS, MAX_RECORDING_FAILURES, LiveSession
from src.live.types import CaptureEvent, CaptureEventKind, CaptureSource, CaptureState, LiveSettings, PcmChunk


class FakeAdapter:
    def start(self, on_chunk, on_event):
        self.on_chunk = on_chunk
        self.on_event = on_event

    def pause(self):
        return None

    def stop(self):
        return None

    def emit(self, chunk):
        self.on_chunk(chunk)

    def fail(self, source):
        self.on_event(CaptureEvent(CaptureEventKind.DEVICE_REMOVED, source, 0, 1, "removed"))


class StartDeniedAdapter(FakeAdapter):
    def start(self, on_chunk, on_event):
        super().start(on_chunk, on_event)
        on_event(CaptureEvent(CaptureEventKind.PERMISSION_DENIED, CaptureSource.SYSTEM, 0, 1, "denied"))


class FakeScheduler:
    def __init__(self, on_error=None):
        self._on_error = on_error

    def submit(self, chunk):
        return None

    def flush(self):
        return None

    def close(self):
        return None

    def fail(self):
        self._on_error(RuntimeError("decode failed"))


def source_chunk(source, *, offset=0, timestamp_ns=1, frame_count=4_800):
    return PcmChunk(
        source, 48_000, 1, offset,
        np.ones((frame_count, 1), dtype=np.float32), timestamp_ns,
    )


def test_removed_system_source_does_not_stop_microphone(tmp_path):
    mic = FakeAdapter()
    system = FakeAdapter()
    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False),
        {CaptureSource.MIC: mic, CaptureSource.SYSTEM: system},
        scheduler_factory=lambda source, on_final, on_partial, on_error: FakeScheduler(),
    )

    session.start()
    system.fail(CaptureSource.SYSTEM)
    mic.emit(
        PcmChunk(CaptureSource.MIC, 48_000, 1, 0, np.ones((4_800, 1), dtype=np.float32), 1)
    )

    status = session.status()
    assert status.state is CaptureState.RECORDING
    assert status.active_sources == {CaptureSource.MIC}
    assert status.failed_sources == {CaptureSource.SYSTEM}


def test_asr_error_is_reported_without_stopping_its_source(tmp_path):
    adapter = FakeAdapter()
    events = []
    scheduler = None

    def scheduler_factory(source, on_final, on_partial, on_error):
        nonlocal scheduler
        scheduler = FakeScheduler(on_error)
        return scheduler

    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False),
        {CaptureSource.SYSTEM: adapter},
        scheduler_factory=scheduler_factory,
    )
    session.subscribe(events.append)
    session.start()
    scheduler.fail()

    assert session.status().active_sources == {CaptureSource.SYSTEM}
    assert events[-1].source is CaptureSource.SYSTEM
    assert events[-1].detail == "decode failed"


def test_startup_permission_event_does_not_leave_source_active(tmp_path):
    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False),
        {CaptureSource.SYSTEM: StartDeniedAdapter()},
        scheduler_factory=lambda source, on_final, on_partial, on_error: FakeScheduler(),
    )

    session.start()

    assert session.status().state is CaptureState.FAILED
    assert session.status().active_sources == set()


def test_mix_failure_is_throttled_without_stopping_source_recording_or_asr(tmp_path):
    class RecordingScheduler(FakeScheduler):
        def __init__(self):
            super().__init__()
            self.submitted = []

        def submit(self, chunk):
            self.submitted.append(chunk)

    class FailingMixRecorder:
        def __init__(self, *args):
            self.written = []

        def write(self, chunk):
            self.written.append(chunk)

        def write_mix(self, chunk):
            raise RuntimeError("mix writer failed")

        def close(self):
            return {}

    mic = FakeAdapter()
    system = FakeAdapter()
    schedulers = {}
    updates = []

    def scheduler_factory(source, on_final, on_partial, on_error):
        scheduler = RecordingScheduler()
        schedulers[source] = scheduler
        return scheduler

    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=True),
        {CaptureSource.MIC: mic, CaptureSource.SYSTEM: system},
        scheduler_factory=scheduler_factory,
        recorder_factory=FailingMixRecorder,
    )
    session.subscribe(updates.append)
    session.start()
    mic.emit(source_chunk(CaptureSource.MIC, timestamp_ns=1))
    system.emit(source_chunk(CaptureSource.SYSTEM, timestamp_ns=1))
    mic.emit(source_chunk(CaptureSource.MIC, offset=4_800, timestamp_ns=1))
    system.emit(source_chunk(CaptureSource.SYSTEM, offset=4_800, timestamp_ns=1))

    assert len(schedulers[CaptureSource.MIC].submitted) == 2
    assert len(schedulers[CaptureSource.SYSTEM].submitted) == 2
    assert session.status().active_sources == {CaptureSource.MIC, CaptureSource.SYSTEM}
    assert [event.detail for event in updates if isinstance(event, CaptureEvent)] == [
        "Mixed audio recording disabled for this session: mix writer failed. "
        "Separate microphone and system recording and recognition continue."
    ]


def test_staggered_source_callbacks_produce_timestamp_aligned_mix(tmp_path):
    class CollectingRecorder:
        def __init__(self, *args):
            self.mixes = []

        def write(self, chunk):
            return None

        def write_mix(self, chunk):
            self.mixes.append(chunk)

        def close(self):
            return {}

    mic = FakeAdapter()
    system = FakeAdapter()
    recorders = []

    def recorder_factory(*args):
        recorder = CollectingRecorder(*args)
        recorders.append(recorder)
        return recorder

    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=True),
        {CaptureSource.MIC: mic, CaptureSource.SYSTEM: system},
        scheduler_factory=lambda source, on_final, on_partial, on_error: FakeScheduler(),
        recorder_factory=recorder_factory,
    )
    session.start()
    mic.emit(source_chunk(CaptureSource.MIC, offset=100, timestamp_ns=1_000_000_000, frame_count=4))
    system.emit(source_chunk(CaptureSource.SYSTEM, offset=200, timestamp_ns=1_000_041_667, frame_count=2))

    assert len(recorders[0].mixes) == 1
    assert recorders[0].mixes[0].frames.shape == (4, 1)


def test_distinct_device_timestamp_epochs_produce_normal_mix(tmp_path):
    class CollectingRecorder:
        def __init__(self, *args):
            self.mixes = []

        def write(self, chunk):
            return None

        def write_mix(self, chunk):
            self.mixes.append(chunk)

        def close(self):
            return {}

    mic = FakeAdapter()
    system = FakeAdapter()
    recorders = []

    def recorder_factory(*args):
        recorder = CollectingRecorder(*args)
        recorders.append(recorder)
        return recorder

    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=True),
        {CaptureSource.MIC: mic, CaptureSource.SYSTEM: system},
        scheduler_factory=lambda source, on_final, on_partial, on_error: FakeScheduler(),
        recorder_factory=recorder_factory,
    )
    session.start()
    mic.emit(source_chunk(CaptureSource.MIC, timestamp_ns=10_000_000_000, frame_count=480))
    system.emit(source_chunk(CaptureSource.SYSTEM, timestamp_ns=9_000_000_000_000, frame_count=480))

    assert len(recorders[0].mixes) == 1
    assert recorders[0].mixes[0].frames.shape == (480, 1)
    assert session._mix_recording_enabled is True


def test_small_normalized_clock_jitter_and_drift_keeps_mix_bounded(tmp_path):
    class CollectingRecorder:
        def __init__(self, *args):
            self.mixes = []

        def write(self, chunk):
            return None

        def write_mix(self, chunk):
            self.mixes.append(chunk)

        def close(self):
            return {}

    mic = FakeAdapter()
    system = FakeAdapter()
    recorders = []

    def recorder_factory(*args):
        recorder = CollectingRecorder(*args)
        recorders.append(recorder)
        return recorder

    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=True),
        {CaptureSource.MIC: mic, CaptureSource.SYSTEM: system},
        scheduler_factory=lambda source, on_final, on_partial, on_error: FakeScheduler(),
        recorder_factory=recorder_factory,
    )
    session.start()
    mic.emit(source_chunk(CaptureSource.MIC, timestamp_ns=1_000_000_000, frame_count=480))
    system.emit(source_chunk(CaptureSource.SYSTEM, timestamp_ns=8_000_000_000, frame_count=480))
    mic.emit(source_chunk(CaptureSource.MIC, offset=480, timestamp_ns=1_010_000_000, frame_count=480))
    system.emit(source_chunk(CaptureSource.SYSTEM, offset=480, timestamp_ns=8_010_300_000, frame_count=480))

    assert [mix.frames.shape for mix in recorders[0].mixes] == [(480, 1), (494, 1)]
    assert session._mix_recording_enabled is True


def test_large_skew_disables_mix_once_without_interrupting_source_recording_or_asr(tmp_path):
    class RecordingScheduler(FakeScheduler):
        def __init__(self):
            super().__init__()
            self.submitted = []

        def submit(self, chunk):
            self.submitted.append(chunk)

    class CollectingRecorder:
        def __init__(self, *args):
            self.written = []
            self.mixes = []

        def write(self, chunk):
            self.written.append(chunk)

        def write_mix(self, chunk):
            self.mixes.append(chunk)

        def close(self):
            return {}

    mic = FakeAdapter()
    system = FakeAdapter()
    schedulers = {}
    recorders = []
    updates = []

    def scheduler_factory(source, on_final, on_partial, on_error):
        scheduler = RecordingScheduler()
        schedulers[source] = scheduler
        return scheduler

    def recorder_factory(*args):
        recorder = CollectingRecorder(*args)
        recorders.append(recorder)
        return recorder

    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=True),
        {CaptureSource.MIC: mic, CaptureSource.SYSTEM: system},
        scheduler_factory=scheduler_factory,
        recorder_factory=recorder_factory,
    )
    session.subscribe(updates.append)
    session.start()
    mic.emit(source_chunk(CaptureSource.MIC, timestamp_ns=1, frame_count=480))
    system.emit(source_chunk(CaptureSource.SYSTEM, timestamp_ns=15_000_000_001, frame_count=480))
    mic.emit(source_chunk(CaptureSource.MIC, offset=480, timestamp_ns=10_000_001, frame_count=480))
    system.emit(source_chunk(CaptureSource.SYSTEM, offset=480, timestamp_ns=16_500_000_001, frame_count=480))

    assert len(recorders[0].mixes) == 1
    assert len(recorders[0].written) == 4
    assert len(schedulers[CaptureSource.MIC].submitted) == 2
    assert len(schedulers[CaptureSource.SYSTEM].submitted) == 2
    mix_notices = [
        event for event in updates
        if isinstance(event, CaptureEvent) and "Mixed audio recording disabled" in event.detail
    ]
    assert len(mix_notices) == 1
    assert mix_notices[0].source is CaptureSource.MIC
    assert session._mix_recording_enabled is False


def test_missing_peer_cannot_grow_mix_queue_or_mix_recording_without_bound(tmp_path):
    class RecordingScheduler(FakeScheduler):
        def __init__(self):
            super().__init__()
            self.submitted = []

        def submit(self, chunk):
            self.submitted.append(chunk)

    class CollectingRecorder:
        def __init__(self, *args):
            self.written = []
            self.mixes = []

        def write(self, chunk):
            self.written.append(chunk)

        def write_mix(self, chunk):
            self.mixes.append(chunk)

        def close(self):
            return {}

    mic = FakeAdapter()
    system = FakeAdapter()
    schedulers = {}
    recorders = []

    def scheduler_factory(source, on_final, on_partial, on_error):
        scheduler = RecordingScheduler()
        schedulers[source] = scheduler
        return scheduler

    def recorder_factory(*args):
        recorder = CollectingRecorder(*args)
        recorders.append(recorder)
        return recorder

    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=True),
        {CaptureSource.MIC: mic, CaptureSource.SYSTEM: system},
        scheduler_factory=scheduler_factory,
        recorder_factory=recorder_factory,
    )
    session.start()
    for index in range(MAX_PENDING_MIX_CHUNKS + 5):
        mic.emit(source_chunk(CaptureSource.MIC, offset=index * 480, timestamp_ns=index * 10_000_000, frame_count=480))

    # A peer that never delivers audio must not starve the mix track: the
    # queue stays bounded, mixing continues with the source that is live, and
    # the session keeps mixed recording enabled (issue #42).
    assert all(len(pending) <= MAX_PENDING_MIX_CHUNKS + 1 for pending in session._mix_inputs.values())
    assert session._mix_recording_enabled is True
    assert CaptureSource.SYSTEM in session._mix_stalled_sources
    assert len(recorders[0].written) == MAX_PENDING_MIX_CHUNKS + 5
    assert len(recorders[0].mixes) == MAX_PENDING_MIX_CHUNKS + 5
    assert len(schedulers[CaptureSource.MIC].submitted) == MAX_PENDING_MIX_CHUNKS + 5


def test_repeatedly_failing_source_recording_is_disabled_instead_of_retried(tmp_path):
    """A writer that cannot open its file will not start working on chunk 5000.

    Issue #48 produced 15 255 identical "recording write failed" lines in one
    session because every chunk re-attempted the doomed segment open.
    """
    class AlwaysFailingRecorder:
        def __init__(self, *args):
            self.attempts = 0

        def write(self, chunk):
            self.attempts += 1
            raise RuntimeError("Format not recognised")

        def write_mix(self, chunk):
            return None

        def close(self):
            return {}

    mic = FakeAdapter()
    recorders = []
    updates = []

    def recorder_factory(*args):
        recorder = AlwaysFailingRecorder(*args)
        recorders.append(recorder)
        return recorder

    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False),
        {CaptureSource.MIC: mic},
        scheduler_factory=lambda source, on_final, on_partial, on_error: FakeScheduler(),
        recorder_factory=recorder_factory,
    )
    session.subscribe(updates.append)
    session.start()
    for index in range(MAX_RECORDING_FAILURES + 20):
        mic.emit(source_chunk(CaptureSource.MIC, offset=index * 4_800, timestamp_ns=index + 1))

    assert recorders[0].attempts == MAX_RECORDING_FAILURES
    assert session.status().active_sources == {CaptureSource.MIC}
    assert session.status().state is CaptureState.RECORDING
    disabled = [
        event for event in updates
        if isinstance(event, CaptureEvent) and "recording disabled" in event.detail.casefold()
    ]
    assert len(disabled) == 1
    assert disabled[0].source is CaptureSource.MIC


def test_a_recovering_source_recording_is_not_disabled(tmp_path):
    """Only consecutive failures count; a transient write error must not kill the track."""
    class FlakyRecorder:
        def __init__(self, *args):
            self.written = []
            self.fail_at = {0, 3}

        def write(self, chunk):
            index = len(self.written)
            if index in self.fail_at:
                self.written.append(None)
                raise RuntimeError("temporary disk hiccup")
            self.written.append(chunk)

        def write_mix(self, chunk):
            return None

        def close(self):
            return {}

    mic = FakeAdapter()
    recorders = []

    def recorder_factory(*args):
        recorder = FlakyRecorder(*args)
        recorders.append(recorder)
        return recorder

    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False),
        {CaptureSource.MIC: mic},
        scheduler_factory=lambda source, on_final, on_partial, on_error: FakeScheduler(),
        recorder_factory=recorder_factory,
    )
    session.start()
    for index in range(MAX_RECORDING_FAILURES + 10):
        mic.emit(source_chunk(CaptureSource.MIC, offset=index * 4_800, timestamp_ns=index + 1))

    assert len(recorders[0].written) == MAX_RECORDING_FAILURES + 10
