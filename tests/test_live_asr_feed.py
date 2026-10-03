"""What the session hands to recognition: mono 16 kHz audio on the session timeline."""

import numpy as np

from src.live.session import LiveSession
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


class CollectingScheduler:
    def __init__(self):
        self.submitted = []

    def submit(self, chunk):
        self.submitted.append(chunk)

    def flush(self):
        return None

    def close(self, timeout=None):
        return True


def build(tmp_path, sources=(CaptureSource.MIC,)):
    adapters = {source: FakeAdapter() for source in sources}
    schedulers = {}

    def factory(source, on_final, on_partial, on_error):
        schedulers[source] = CollectingScheduler()
        return schedulers[source]

    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False, record_source_audio=False),
        adapters,
        scheduler_factory=factory,
    )
    session.start()
    return session, adapters, schedulers


def asr_audio(scheduler):
    return np.concatenate([chunk.frames[:, 0] for chunk in scheduler.submitted])


def test_speech_on_the_second_channel_reaches_recognition(tmp_path):
    """Only channel 0 was passed on, so a stereo source with the talker on the
    right channel (a USB interface with the mic on input 2) was never heard."""
    session, adapters, schedulers = build(tmp_path)
    frames = np.zeros((4_800, 2), dtype=np.float32)
    frames[:, 1] = 0.5

    adapters[CaptureSource.MIC].on_chunk(PcmChunk(CaptureSource.MIC, 48_000, 2, 0, frames, 1))

    audio = asr_audio(schedulers[CaptureSource.MIC])
    assert np.allclose(audio[100:-100], 0.25, atol=1e-3)
