import numpy as np
import pytest

from src.live.recorder import SessionRecorder
from src.live.types import CaptureSource, PcmChunk


def chunk(source: CaptureSource, sample_rate: int = 48_000, channels: int = 2) -> PcmChunk:
    return PcmChunk(source, sample_rate, channels, 0, np.ones((3, channels), np.float32), 1)


class Writer:
    def __init__(self, path, **kwargs):
        self.path = path
        self.kwargs = kwargs
        self.blocks = []
        self.closed = False

    def write(self, frames):
        self.blocks.append(frames)

    def close(self):
        self.closed = True


def test_recorder_preserves_source_rate_channels_and_selected_artifacts(tmp_path):
    writers = []

    def factory(path, **kwargs):
        writer = Writer(path, **kwargs)
        writers.append(writer)
        return writer

    recorder = SessionRecorder(tmp_path, record_sources=True, record_mix=False, writer_factory=factory)
    recorder.write(chunk(CaptureSource.MIC))
    recorder.write(chunk(CaptureSource.SYSTEM, sample_rate=44_100, channels=1))
    paths = recorder.close()

    assert paths == {
        CaptureSource.MIC: tmp_path / "mic.flac",
        CaptureSource.SYSTEM: tmp_path / "system.flac",
    }
    assert [(writer.kwargs["samplerate"], writer.kwargs["channels"]) for writer in writers] == [
        (48_000, 2),
        (44_100, 1),
    ]
    assert all(writer.kwargs["format"] == "FLAC" for writer in writers)
    assert all(writer.kwargs["subtype"] == "PCM_24" for writer in writers)
    assert all(writer.closed for writer in writers)


def test_recorder_rejects_oversized_blocks_and_rolls_flac_segments(tmp_path):
    writers = []

    def factory(path, **kwargs):
        writer = Writer(path, **kwargs)
        writers.append(writer)
        return writer

    recorder = SessionRecorder(
        tmp_path,
        writer_factory=factory,
        max_block_frames=3,
        segment_max_bytes=18,
    )
    with pytest.raises(ValueError, match="frame limit"):
        recorder.write(PcmChunk(CaptureSource.MIC, 48_000, 2, 0, np.ones((4, 2), np.float32), 1))
    recorder.write(PcmChunk(CaptureSource.MIC, 48_000, 2, 0, np.ones((3, 2), np.float32), 1))
    recorder.write(PcmChunk(CaptureSource.MIC, 48_000, 2, 3, np.ones((2, 2), np.float32), 2))
    recorder.close()

    assert [writer.path.name for writer in writers] == ["mic.flac", "mic-002.flac"]
    assert [sum(len(block) for block in writer.blocks) for writer in writers] == [3, 2]
    artifact = recorder.artifacts()["mic"]
    assert artifact["codec"] == "FLAC PCM_24"
    assert artifact["rate"] == 48_000
    assert artifact["channels"] == 2
    assert artifact["frames"] == 5
    assert artifact["bytes"] == 30
    assert len(artifact["segments"]) == 2


def test_recorder_clamps_channels_to_the_flac_ceiling(tmp_path):
    """libsndfile answers a 32-channel FLAC with a bare "Format not recognised".

    Capture is stereo-capped upstream, but the write side must not depend on
    that: any backend handing us more than eight channels has to degrade to a
    usable recording instead of failing every chunk (issue #48).
    """
    writers = []

    def factory(path, **kwargs):
        writer = Writer(path, **kwargs)
        writers.append(writer)
        return writer

    recorder = SessionRecorder(tmp_path, record_mix=False, writer_factory=factory)
    recorder.write(PcmChunk(CaptureSource.MIC, 48_000, 32, 0, np.ones((3, 32), np.float32), 1))
    recorder.write(PcmChunk(CaptureSource.MIC, 48_000, 32, 3, np.ones((2, 32), np.float32), 2))
    recorder.close()

    assert [writer.kwargs["channels"] for writer in writers] == [8]
    assert all(block.shape[1] == 8 for block in writers[0].blocks)
    artifact = recorder.artifacts()["mic"]
    assert artifact["channels"] == 8
    assert artifact["frames"] == 5
    assert artifact["bytes"] == 5 * 8 * 3
