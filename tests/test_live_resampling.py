"""Rate conversion on the live path: anti-aliased, continuous across chunks, timing-true."""

import numpy as np

from src.core.asr.types import StreamResampler, normalize_window_audio
from src.live.session import LiveSession
from src.live.timeline import AlignedMixer
from src.live.types import CaptureSource, LiveSettings, PcmChunk


def tone(frequency, seconds, rate, amplitude=0.5):
    t = np.arange(int(seconds * rate)) / rate
    return (amplitude * np.sin(2 * np.pi * frequency * t)).astype(np.float32)


def rms(samples):
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


def peak_frequency(samples, rate):
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    return np.fft.rfftfreq(len(samples), 1 / rate)[int(np.argmax(spectrum))]


class FakeAdapter:
    def start(self, on_chunk, on_event):
        self.on_chunk = on_chunk

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


def recognized(tmp_path, signal, rate, chunk=480):
    adapter = FakeAdapter()
    scheduler = CollectingScheduler()
    session = LiveSession(
        tmp_path,
        LiveSettings(record_mix_audio=False, record_source_audio=False),
        {CaptureSource.MIC: adapter},
        scheduler_factory=lambda source, on_final, on_partial, on_error: scheduler,
    )
    session.start()
    for start in range(0, len(signal), chunk):
        part = signal[start:start + chunk, None].copy()
        adapter.on_chunk(PcmChunk(CaptureSource.MIC, rate, 1, start, part, 1 + start))
    session.stop()
    return scheduler.submitted


def test_a_tone_above_the_model_nyquist_does_not_alias_into_speech(tmp_path):
    """Per-chunk linear interpolation folded a 10 kHz tone to 6 kHz at 63 % of its level."""
    chunks = recognized(tmp_path, tone(10_000, 1.0, 48_000), 48_000)
    audio = np.concatenate([chunk.frames[:, 0] for chunk in chunks])

    assert rms(audio[1_600:]) < 0.01


def test_speech_band_audio_keeps_its_level_frequency_and_length(tmp_path):
    chunks = recognized(tmp_path, tone(1_000, 1.0, 48_000), 48_000)
    audio = np.concatenate([chunk.frames[:, 0] for chunk in chunks])

    assert abs(len(audio) - 16_000) <= 1
    assert abs(rms(audio[1_600:-1_600]) - 0.5 / np.sqrt(2)) < 0.01
    assert abs(peak_frequency(audio[1_600:-1_600], 16_000) - 1_000) < 5


def test_recognition_chunks_tile_the_timeline_without_gaps_or_overlaps(tmp_path):
    chunks = recognized(tmp_path, tone(1_000, 0.5, 44_100), 44_100, chunk=441)

    ends = [chunk.sample_offset + len(chunk.frames) for chunk in chunks[:-1]]
    assert [chunk.sample_offset for chunk in chunks[1:]] == ends
    assert abs(sum(len(chunk.frames) for chunk in chunks) - 8_000) <= 1


def test_streaming_in_chunks_matches_one_pass_over_the_whole_signal():
    """Each chunk used to be resampled on its own, endpoints included, so every
    boundary warped the waveform; a stream must not depend on how it is cut."""
    signal = tone(1_000, 0.5, 44_100)[:, None]
    whole = StreamResampler(44_100, 16_000).process(signal)
    chunked_resampler = StreamResampler(44_100, 16_000)
    chunked = np.concatenate([
        chunked_resampler.process(signal[start:start + 441]) for start in range(0, len(signal), 441)
    ])

    assert chunked.shape == whole.shape
    assert np.max(np.abs(chunked - whole)) < 1e-5


def test_whole_window_conversion_is_anti_aliased_and_not_delayed():
    converted = normalize_window_audio(tone(10_000, 1.0, 48_000), 48_000)
    assert len(converted) == 16_000
    assert rms(converted[800:-800]) < 0.01

    converted = normalize_window_audio(tone(1_000, 1.0, 48_000), 48_000)
    expected = tone(1_000, 1.0, 16_000)
    assert np.max(np.abs(converted[800:-800] - expected[800:-800])) < 0.01


def test_constant_input_stays_constant():
    resampler = StreamResampler(48_000, 44_100)
    output = np.concatenate([resampler.process(np.full((480, 2), 0.25, np.float32)) for _ in range(10)])

    assert np.allclose(output, 0.25, atol=1e-6)


def test_mixer_resamples_a_source_without_aliasing():
    mixer = AlignedMixer(max_output_frames=48_000)
    mixer.mix({CaptureSource.MIC: PcmChunk(CaptureSource.MIC, 16_000, 1, 0, np.zeros((160, 1), np.float32), 0)})
    signal = tone(10_000, 0.5, 48_000)
    blocks = []
    for start in range(0, len(signal), 480):
        part = signal[start:start + 480, None].copy()
        timestamp = round((start / 48_000 + 0.01) * 1_000_000_000)
        blocks.append(mixer.mix({CaptureSource.SYSTEM: PcmChunk(CaptureSource.SYSTEM, 48_000, 1, start, part, timestamp)}))
    mixed = np.concatenate([block.frames[:, 0] for block in blocks])

    assert rms(mixed[800:]) < 0.01
