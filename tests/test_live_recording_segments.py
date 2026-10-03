"""A long session rolls its recordings into segments; none of them may be forgotten."""

import base64

import numpy as np
import soundfile as sf

from src.core.diarization.base import SpeakerSegment
from src.live.recorder import SessionRecorder
from src.live.session import LiveSession
from src.live.types import CaptureSource, DiarizationMode, LiveSettings, PcmChunk, TranscriptEvent
from src.services.live_worker_service import LiveWorkerService

SEGMENT_BYTES = 48_000 * 3  # one second of 48 kHz mono PCM_24 per segment


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


def small_segments(session_dir, sources, record_mix):
    return SessionRecorder(session_dir, sources, record_mix, segment_max_bytes=SEGMENT_BYTES)


def feed(adapter, seconds, source=CaptureSource.MIC):
    for index in range(int(seconds * 10)):
        frames = np.full((4_800, 1), 0.1, dtype=np.float32)
        adapter.on_chunk(PcmChunk(source, 48_000, 1, index * 4_800, frames, 1 + index * 100_000_000))


def test_session_result_lists_every_segment_of_a_rolled_recording(tmp_path):
    """Only the first file of each track was returned: after ~15 min of 48 kHz
    stereo the rest of the session's audio was missing from the result."""
    adapter = FakeAdapter()
    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False),
        {CaptureSource.MIC: adapter},
        scheduler_factory=lambda source, on_final, on_partial, on_error: NullScheduler(),
        recorder_factory=small_segments,
    )
    session.start()
    feed(adapter, 2.5)

    result = session.stop()

    names = [path.name for path in result.recording_files["mic"]]
    assert names == ["mic.flac", "mic-002.flac", "mic-003.flac"]
    assert result.recordings == {CaptureSource.MIC: result.session_dir / "mic.flac"}


def test_after_stop_diarization_hears_every_segment(tmp_path):
    adapter = FakeAdapter()
    heard = []

    class WholeFileDiarizer:
        def diarize(self, path):
            info = sf.info(path)
            heard.append((path, info.frames))
            return [SpeakerSegment(0.0, info.frames / info.samplerate, "A")]

    session = LiveSession(
        tmp_path,
        LiveSettings(diarization_mode=DiarizationMode.AFTER_STOP, record_mix_audio=False),
        {CaptureSource.MIC: adapter},
        scheduler_factory=lambda source, on_final, on_partial, on_error: NullScheduler(),
        recorder_factory=small_segments,
        diarization_factory=lambda backend: WholeFileDiarizer(),
    )
    session.start()
    feed(adapter, 2.5)
    late = TranscriptEvent("mic-late", 0, CaptureSource.MIC, 32_000, 36_000, 1, "late words", "final")
    session._on_final(late)

    session.stop()

    assert [frames for _path, frames in heard] == [120_000]
    labelled = {event.event_id: event.speaker for event in session._journal.latest_events()}
    assert labelled["mic-late"] == "Speaker 1"
    assert not [path for path in session.session_dir.iterdir() if "diarize" in path.name]


def test_worker_reports_every_recording_file(tmp_path):
    """live_stopped.recordings names one file per source; the new
    recording_files lists every segment of every track."""
    messages = []

    class Scheduler(NullScheduler):
        def __init__(self, *args):
            return None

    service = LiveWorkerService(
        lambda message_type, **payload: messages.append({"type": message_type, **payload}),
        scheduler_factory=Scheduler,
    )
    service.start({"type": "live_start", "session_root": str(tmp_path), "sources": ["mic"]})
    session_dir = service.session.session_dir
    # 16 kHz mono: SEGMENT_BYTES holds 3 s per segment.
    service.session._recorder = small_segments(session_dir, {CaptureSource.MIC}, False)
    pcm = base64.b64encode(np.full(1_600, 3_276, dtype=np.int16).tobytes()).decode("ascii")
    for seq in range(40):
        service.audio({
            "type": "live_audio", "source": "mic", "seq": seq, "sample_offset": seq * 1_600,
            "timestamp_ns": seq * 100_000_000, "pcm": pcm,
        })

    service.stop()

    stopped = messages[-1]
    assert stopped["type"] == "live_stopped"
    assert stopped["recordings"] == {"mic": str(session_dir / "mic.flac")}
    assert stopped["recording_files"] == {"mic": [str(session_dir / "mic.flac"), str(session_dir / "mic-002.flac")]}
