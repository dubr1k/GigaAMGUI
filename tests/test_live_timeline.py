import numpy as np

from src.live.timeline import AlignedMixer, SourceTimeline
from src.live.types import CaptureEventKind, CaptureSource, PcmChunk


def chunk(
    offset: int,
    frames: list[float],
    source: CaptureSource = CaptureSource.MIC,
) -> PcmChunk:
    return PcmChunk(
        source,
        48_000,
        1,
        offset,
        np.asarray(frames, dtype=np.float32).reshape(-1, 1).copy(),
        1,
    )


def test_timeline_inserts_silence_for_offset_gap():
    timeline = SourceTimeline(CaptureSource.MIC, sample_rate=48_000, channels=1)

    timeline.ingest(chunk(0, [0.5, 0.5]))
    emitted = timeline.ingest(chunk(5, [0.25]))

    assert emitted[0].frames.tolist() == [[0.0], [0.0], [0.0]]
    assert emitted[0].sample_offset == 2
    assert emitted[-1].sample_offset == 5


def test_timeline_discards_previously_emitted_overlap_and_reports_it():
    events = []
    timeline = SourceTimeline(
        CaptureSource.MIC,
        sample_rate=48_000,
        channels=1,
        on_event=events.append,
    )

    timeline.ingest(chunk(0, [0.1, 0.2, 0.3]))
    emitted = timeline.ingest(chunk(2, [0.3, 0.4, 0.5]))

    assert emitted[0].sample_offset == 3
    assert np.allclose(emitted[0].frames, [[0.4], [0.5]])
    assert events[-1].kind is CaptureEventKind.DISCONTINUITY
    assert "overlap=1" in events[-1].detail


def test_timeline_keeps_emitted_offsets_monotonic_for_gaps_and_overlaps():
    timeline = SourceTimeline(CaptureSource.MIC, sample_rate=48_000, channels=1)
    chunks = [chunk(0, [0.1, 0.2]), chunk(5, [0.3]), chunk(4, [0.4, 0.5, 0.6])]

    emitted = [part for audio in chunks for part in timeline.ingest(audio)]

    assert [part.sample_offset for part in emitted] == [0, 2, 5, 6]
    assert emitted[1].frames.tolist() == [[0.0], [0.0], [0.0]]


def test_timeline_does_not_allocate_silence_for_large_source_gap():
    events = []
    timeline = SourceTimeline(
        CaptureSource.MIC,
        sample_rate=48_000,
        channels=1,
        on_event=events.append,
        max_gap_seconds=0.1,
    )

    first = timeline.ingest(chunk(0, [0.1]))
    resumed = timeline.ingest(chunk(720_000, [0.2]))

    assert [part.frames.shape for part in first + resumed] == [(1, 1), (1, 1)]
    assert resumed[0].sample_offset == 720_000
    assert "gap=719999 discarded" in events[-1].detail


def test_timeline_preserves_monotonic_offsets_for_seeded_gaps_and_overlaps():
    random = np.random.default_rng(0)
    timeline = SourceTimeline(CaptureSource.MIC, sample_rate=48_000, channels=1)
    expected_next = 0
    emitted = []

    for _ in range(20):
        offset = max(0, expected_next + int(random.integers(-2, 4)))
        frame_count = int(random.integers(1, 5))
        parts = timeline.ingest(chunk(offset, random.uniform(-1, 1, frame_count).tolist()))
        emitted.extend(parts)
        if parts:
            expected_next = parts[-1].sample_offset + len(parts[-1].frames)

    assert [part.sample_offset for part in emitted] == sorted(part.sample_offset for part in emitted)
    assert all(part.frames.dtype == np.float32 for part in emitted)


def test_mixer_applies_gains_and_clips_to_audio_range():
    mixer = AlignedMixer(mic_gain=1.0, system_gain=1.0)

    mixed = mixer.mix(
        {
            CaptureSource.MIC: chunk(0, [0.8, -0.8]),
            CaptureSource.SYSTEM: chunk(0, [0.7, -0.7], CaptureSource.SYSTEM),
        }
    )

    assert mixed.frames.tolist() == [[1.0], [-1.0]]


def test_mixer_aligns_staggered_timestamped_chunks_and_pads_silence():
    mixer = AlignedMixer()

    mixed = mixer.mix(
        {
            CaptureSource.MIC: PcmChunk(
                CaptureSource.MIC, 4, 1, 100,
                np.full((4, 1), 0.25, dtype=np.float32), 10_000_000_000,
            ),
            CaptureSource.SYSTEM: PcmChunk(
                CaptureSource.SYSTEM, 4, 1, 200,
                np.full((2, 1), 0.5, dtype=np.float32), 10_500_000_000,
            ),
        }
    )

    assert mixed.timestamp_ns == 10_000_000_000
    assert mixed.frames.tolist() == [[0.25], [0.25], [0.75], [0.75]]


def test_mixer_normalizes_format_and_frame_count_before_mixing():
    mixer = AlignedMixer()

    mixed = mixer.mix(
        {
            CaptureSource.MIC: PcmChunk(
                CaptureSource.MIC, 4, 1, 0,
                np.full((4, 1), 0.25, dtype=np.float32), 1,
            ),
            CaptureSource.SYSTEM: PcmChunk(
                CaptureSource.SYSTEM, 2, 2, 0,
                np.full((2, 2), 0.5, dtype=np.float32), 1,
            ),
        }
    )

    assert mixed.sample_rate == 4
    assert mixed.channels == 1
    assert mixed.frames.shape == (4, 1)
    assert np.allclose(mixed.frames, 0.75)


def test_mixer_treats_missing_source_as_silence():
    mixed = AlignedMixer().mix({CaptureSource.MIC: chunk(12, [0.25, -0.25])})

    # Смещение — позиция на дорожке микса, а не в потоке источника: микс
    # начинается с начала сессии независимо от того, с какого чанка пришёл mic.
    assert mixed.sample_offset == 0
    assert mixed.frames.tolist() == [[0.25], [-0.25]]


def test_mixer_rejects_large_timestamp_skew_before_allocating_padding():
    mixer = AlignedMixer(max_skew_seconds=0.1, max_output_frames=4_800)

    with np.testing.assert_raises_regex(ValueError, "timestamp skew"):
        mixer.mix(
            {
                CaptureSource.MIC: PcmChunk(
                    CaptureSource.MIC, 48_000, 1, 0,
                    np.ones((480, 1), dtype=np.float32), 1,
                ),
                CaptureSource.SYSTEM: PcmChunk(
                    CaptureSource.SYSTEM, 48_000, 1, 0,
                    np.ones((480, 1), dtype=np.float32), 15_000_000_001,
                ),
            }
        )


def paired(index, frames_per_chunk, rate, offset_ns, source, level):
    """Непрерывный поток: чанк N начинается ровно там, где кончился N-1."""
    return PcmChunk(
        source,
        rate,
        1,
        index * frames_per_chunk,
        np.full((frames_per_chunk, 1), level, dtype=np.float32),
        round(index * frames_per_chunk * 1_000_000_000 / rate) + offset_ns,
    )


def test_mixer_does_not_pay_the_source_offset_once_per_pair():
    """issue #50: сдвиг между парами набивался тишиной в каждый блок.

    Два непрерывных потока по 100 фреймов на чанк, system стабильно на 20 мс
    позади. Старый микшер писал 120 фреймов на пару (100 звука + 20 набивки),
    то есть ×1.2 от реального времени; при наблюдавшихся ~90 мс на пару это и
    дало mix.flac ×4.87.
    """
    mixer = AlignedMixer()

    emitted = [
        mixer.mix(
            {
                CaptureSource.MIC: paired(index, 100, 1_000, 0, CaptureSource.MIC, 0.25),
                CaptureSource.SYSTEM: paired(index, 100, 1_000, 20_000_000, CaptureSource.SYSTEM, 0.5),
            }
        )
        for index in range(10)
    ]
    tail = mixer.flush()

    total = sum(len(mixed.frames) for mixed in emitted) + (len(tail.frames) if tail else 0)
    # 1000 фреймов звука плюс один-единственный стартовый сдвиг в 20 фреймов.
    assert total == 1_020
    assert [mixed.sample_offset for mixed in emitted[:3]] == [0, 100, 200]


def test_mixer_keeps_every_microphone_frame_when_the_peer_lags():
    """issue #50: mic «тонул» в миксе — набивка размазывала его по тишине."""
    mixer = AlignedMixer(mic_gain=1.0, system_gain=0.0)

    written = np.concatenate(
        [
            mixer.mix(
                {
                    CaptureSource.MIC: paired(index, 4, 1_000, 0, CaptureSource.MIC, 0.5),
                    CaptureSource.SYSTEM: paired(index, 4, 1_000, 8_000_000, CaptureSource.SYSTEM, 0.5),
                }
            ).frames
            for index in range(5)
        ]
        + [mixer.flush().frames]
    )

    # Микрофон записан целиком и подряд; восемь миллисекунд, на которые
    # system отстаёт, дописаны один раз в хвост, а не по разу на пару.
    assert written[:20].tolist() == [[0.5]] * 20
    assert written[20:].tolist() == [[0.0]] * 8


def test_mixer_output_format_is_fixed_by_the_first_block():
    """Пропавший на секунду mic не должен переводить дорожку на 48 кГц."""
    mixer = AlignedMixer()
    mixer.mix({CaptureSource.MIC: PcmChunk(CaptureSource.MIC, 44_100, 1, 0, np.zeros((441, 1), np.float32), 0)})

    mixed = mixer.mix(
        {
            CaptureSource.SYSTEM: PcmChunk(
                CaptureSource.SYSTEM, 48_000, 2, 0,
                np.full((480, 2), 0.5, dtype=np.float32), 10_000_000,
            )
        }
    )

    assert (mixed.sample_rate, mixed.channels) == (44_100, 1)


def test_mixer_jitter_does_not_accumulate_across_blocks():
    """Дрожание меток прихода знака не имеет — раньше каждый пик добавлял тишину.

    После правки ``_normalize_mix_timestamp`` метки берутся из смещений
    сэмплов и не дрожат вовсе; здесь проверяется, что и сам микшер к дрожанию
    устойчив — 600 фреймов звука дают ровно 600 фреймов дорожки.
    """
    mixer = AlignedMixer()
    jitter = [0, 5_000_000, -5_000_000, 3_000_000, -4_000_000, 0]

    total = sum(
        len(
            mixer.mix(
                {
                    CaptureSource.MIC: paired(index, 100, 1_000, 0, CaptureSource.MIC, 0.25),
                    CaptureSource.SYSTEM: paired(index, 100, 1_000, shift, CaptureSource.SYSTEM, 0.25),
                }
            ).frames
        )
        for index, shift in enumerate(jitter)
    )
    tail = mixer.flush()

    assert total + (len(tail.frames) if tail else 0) == 600
