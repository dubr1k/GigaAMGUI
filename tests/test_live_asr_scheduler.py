import time

import numpy as np

from src.live.asr import LiveAsrScheduler
from src.live.types import CaptureSource, PcmChunk


class FakeBackend:
    def __init__(self):
        self.requests = []

    def transcribe_window(self, audio, sample_rate, offset_samples):
        self.requests.append((audio.copy(), sample_rate, offset_samples))
        start = offset_samples / sample_rate
        return [{"transcription": "recognized speech", "boundaries": (start, start + 0.5)}]


def voiced_chunk(offset: int, seconds: float) -> PcmChunk:
    frames = np.ones((int(seconds * 16_000), 1), dtype=np.float32)
    return PcmChunk(CaptureSource.MIC, 16_000, 1, offset, frames, 1)


def silent_chunk(offset: int, seconds: float = 0.1) -> PcmChunk:
    frames = np.zeros((int(seconds * 16_000), 1), dtype=np.float32)
    return PcmChunk(CaptureSource.MIC, 16_000, 1, offset, frames, 1)


def noise_chunk(offset: int, seconds: float, amplitude: float = 0.012) -> PcmChunk:
    frames = np.full((int(seconds * 16_000), 1), amplitude, dtype=np.float32)
    return PcmChunk(CaptureSource.MIC, 16_000, 1, offset, frames, 1)


def wait_until(predicate):
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("scheduler did not finish work")


def test_new_partial_refreshes_after_the_decode_cadence():
    backend = FakeBackend()
    scheduler = LiveAsrScheduler(backend=backend, partial_delay_seconds=1.5)
    try:
        scheduler.submit(voiced_chunk(0, 6))
        wait_until(lambda: backend.requests)
        scheduler.submit(voiced_chunk(6 * 16_000, 2))

        wait_until(lambda: len(backend.requests) == 2)
        assert backend.requests[-1][2] == 0
    finally:
        scheduler.close()


def test_silent_and_low_energy_noise_never_start_asr_work():
    backend = FakeBackend()
    partials = []
    finals = []
    scheduler = LiveAsrScheduler(backend=backend, on_partial=partials.append, on_final=finals.append)
    try:
        scheduler.submit(silent_chunk(0, 2))
        scheduler.submit(noise_chunk(32_000, 2))
        time.sleep(0.05)

        assert backend.requests == []
        assert partials == []
        assert finals == []
    finally:
        scheduler.close()


def test_new_speech_after_final_emits_a_partial_at_1_5_seconds():
    backend = FakeBackend()
    partials = []
    finals = []
    scheduler = LiveAsrScheduler(backend=backend, on_partial=partials.append, on_final=finals.append)
    try:
        scheduler.submit(voiced_chunk(0, 2))
        scheduler.submit(silent_chunk(2 * 16_000, 3.0))
        wait_until(lambda: finals)

        scheduler.submit(voiced_chunk(5 * 16_000, 1.5))

        wait_until(lambda: any(event.sample_start == 5 * 16_000 for event in partials))
        assert partials[-1].status == "partial"
        assert partials[-1].sample_start == 5 * 16_000
    finally:
        scheduler.close()


def test_2_99_seconds_of_silence_does_not_commit_a_final_event():
    backend = FakeBackend()
    partials = []
    finals = []
    scheduler = LiveAsrScheduler(
        backend=backend,
        partial_delay_seconds=1.5,
        on_partial=partials.append,
        on_final=finals.append,
    )
    try:
        scheduler.submit(voiced_chunk(0, 2))
        scheduler.submit(silent_chunk(2 * 16_000, 2.99))

        time.sleep(0.05)
        assert finals == []
    finally:
        scheduler.close()


def test_3_seconds_of_silence_commits_a_final_event():
    backend = FakeBackend()
    finals = []
    scheduler = LiveAsrScheduler(backend=backend, on_final=finals.append)
    try:
        scheduler.submit(voiced_chunk(0, 2))
        scheduler.submit(silent_chunk(2 * 16_000, 3.0))

        wait_until(lambda: finals)
        assert finals[0].status == "final"
        assert finals[0].sample_start == 0
        assert finals[0].sample_end == int(5 * 16_000)
        assert finals[0].text == "recognized speech"
        assert finals[0].paragraph_break_after is True
    finally:
        scheduler.close()


def test_accumulated_silence_commits_even_when_capture_offsets_do_not_advance():
    backend = FakeBackend()
    finals = []
    scheduler = LiveAsrScheduler(backend=backend, on_final=finals.append)
    try:
        scheduler.submit(voiced_chunk(0, 2))
        for _ in range(30):
            scheduler.submit(silent_chunk(2 * 16_000, 0.1))

        wait_until(lambda: finals)
        assert finals[0].status == "final"
    finally:
        scheduler.close()


def test_partial_uses_a_bounded_rolling_context_and_revises_it():
    backend = FakeBackend()
    partials = []
    scheduler = LiveAsrScheduler(
        backend=backend,
        partial_delay_seconds=1.5,
        on_partial=partials.append,
    )
    try:
        scheduler.submit(voiced_chunk(0, 12))
        wait_until(lambda: partials)
        scheduler.submit(voiced_chunk(12 * 16_000, 2))
        wait_until(lambda: len(partials) == 2)

        assert partials[-1].revision == partials[0].revision + 1
        assert partials[-1].supersedes == partials[0].revision
        assert backend.requests[-1][2] == 2 * 16_000
        assert len(backend.requests[-1][0]) == 12 * 16_000
    finally:
        scheduler.close()


def test_partial_retains_stable_prefix_when_later_window_regresses_to_repetition():
    class RevisingBackend(FakeBackend):
        def __init__(self):
            super().__init__()
            self.transcripts = iter((
                "We need to review the plan tomorrow",
                "We need to review the plan tomorrow morning",
                "We need to review la la la",
            ))

        def transcribe_window(self, audio, sample_rate, offset_samples):
            self.requests.append((audio.copy(), sample_rate, offset_samples))
            return [{"transcription": next(self.transcripts), "boundaries": (0, 1)}]

    partials = []
    scheduler = LiveAsrScheduler(RevisingBackend(), partial_delay_seconds=0.1, on_partial=partials.append)
    try:
        scheduler.submit(voiced_chunk(0, 1.5))
        wait_until(lambda: len(partials) == 1)
        scheduler.submit(voiced_chunk(24_000, 1.5))
        wait_until(lambda: len(partials) == 2)
        scheduler.submit(voiced_chunk(48_000, 1.5))
        wait_until(lambda: len(partials) == 3)

        assert partials[-1].text == "We need to review the plan tomorrow morning"
    finally:
        scheduler.close()


def test_final_decode_can_correct_a_stable_partial_prefix():
    class CorrectingBackend(FakeBackend):
        def __init__(self):
            super().__init__()
            self.transcripts = iter(("We need to review the plan", "We need to cancel the plan"))

        def transcribe_window(self, audio, sample_rate, offset_samples):
            self.requests.append((audio.copy(), sample_rate, offset_samples))
            return [{"transcription": next(self.transcripts), "boundaries": (0, 1)}]

    partials = []
    finals = []
    scheduler = LiveAsrScheduler(CorrectingBackend(), on_partial=partials.append, on_final=finals.append)
    try:
        scheduler.submit(voiced_chunk(0, 2))
        wait_until(lambda: partials)
        scheduler.submit(silent_chunk(32_000, 3.0))
        wait_until(lambda: finals)

        assert finals[-1].text == "We need to cancel the plan"
    finally:
        scheduler.close()


def test_terminal_punctuation_does_not_commit_a_sentence_without_silence():
    class TerminalBackend(FakeBackend):
        def transcribe_window(self, audio, sample_rate, offset_samples):
            self.requests.append((audio.copy(), sample_rate, offset_samples))
            return [{"transcription": "This is complete.", "boundaries": (0, 1)}]

    finals = []
    scheduler = LiveAsrScheduler(TerminalBackend(), on_final=finals.append)
    try:
        scheduler.submit(voiced_chunk(0, 6))
        wait_until(lambda: scheduler.refresh_seconds < 1.5)
        scheduler.submit(voiced_chunk(6 * 16_000, 0.5))

        wait_until(lambda: len(scheduler._backend.requests) == 2)
        assert finals == []
    finally:
        scheduler.close()


def test_one_word_final_fragment_is_not_published():
    class OneWordBackend(FakeBackend):
        def transcribe_window(self, audio, sample_rate, offset_samples):
            return [{"transcription": "Hello", "boundaries": (0, 1)}]

    finals = []
    scheduler = LiveAsrScheduler(OneWordBackend(), on_final=finals.append)
    try:
        scheduler.submit(voiced_chunk(0, 2))
        scheduler.submit(silent_chunk(2 * 16_000, 0.9))
        time.sleep(0.05)

        assert finals == []
    finally:
        scheduler.close()


def test_slow_decode_lengthens_refresh_within_configured_limit():
    scheduler = LiveAsrScheduler(backend=FakeBackend())
    try:
        scheduler.record_decode_duration(2.0)

        assert scheduler.refresh_seconds == 1.5
    finally:
        scheduler.close()


def quiet_room(offset: int, seconds: float, amplitude: float = 0.0005) -> PcmChunk:
    rng = np.random.default_rng(offset)
    frames = (rng.standard_normal((int(seconds * 16_000), 1)) * amplitude).astype(np.float32)
    return PcmChunk(CaptureSource.MIC, 16_000, 1, offset, frames, 1)


def quiet_syllables(offset: int, seconds: float) -> PcmChunk:
    """Speech from a quiet microphone: 40 ms syllables at RMS 0.02 every 100 ms.

    Each syllable clears the attack threshold, but averaged over a 100 ms
    chunk (what GigaAM Liquid sends) the RMS is ~0.013 and never does.
    """
    samples = int(seconds * 16_000)
    frames = np.full((samples, 1), 0.0005, dtype=np.float32)
    for start in range(0, samples, 1600):
        frames[start:start + 640] = 0.02
    return PcmChunk(CaptureSource.MIC, 16_000, 1, offset, frames, 1)


def submit_in_chunks(scheduler, chunk: PcmChunk, size: int = 1600) -> None:
    for start in range(0, len(chunk.frames), size):
        scheduler.submit(PcmChunk(
            chunk.source, chunk.sample_rate, 1, chunk.sample_offset + start,
            chunk.frames[start:start + size].copy(), chunk.timestamp_ns,
        ))


def test_short_quiet_phrase_in_100ms_chunks_is_published():
    """A real session lost «Привет! Как у тебя дела?» this way: decoded, then dropped."""
    backend = FakeBackend()
    finals = []
    scheduler = LiveAsrScheduler(backend=backend, on_final=finals.append)
    try:
        submit_in_chunks(scheduler, quiet_room(0, 2))
        submit_in_chunks(scheduler, quiet_syllables(32_000, 0.8))
        submit_in_chunks(scheduler, quiet_room(44_800, 3.2))

        wait_until(lambda: finals)
        assert finals[0].text == "recognized speech"
    finally:
        scheduler.close()


def test_short_phrase_is_shown_as_a_partial_at_the_pause_before_the_final():
    backend = FakeBackend()
    partials = []
    finals = []
    scheduler = LiveAsrScheduler(backend=backend, on_partial=partials.append, on_final=finals.append)
    try:
        submit_in_chunks(scheduler, quiet_room(0, 1))
        submit_in_chunks(scheduler, voiced_chunk(16_000, 1.0))
        submit_in_chunks(scheduler, quiet_room(32_000, 0.6))

        wait_until(lambda: partials)
        assert finals == []
        assert partials[0].text == "recognized speech"
    finally:
        scheduler.close()


def test_run_includes_the_onset_that_preceded_the_gate():
    backend = FakeBackend()
    scheduler = LiveAsrScheduler(backend=backend)
    try:
        submit_in_chunks(scheduler, quiet_room(0, 2))
        submit_in_chunks(scheduler, voiced_chunk(32_000, 2))

        wait_until(lambda: backend.requests)
        assert 32_000 - 16_000 <= backend.requests[0][2] < 32_000
    finally:
        scheduler.close()


class ScriptedBackend(FakeBackend):
    def __init__(self, *transcripts):
        super().__init__()
        self.transcripts = iter(transcripts)

    def transcribe_window(self, audio, sample_rate, offset_samples):
        self.requests.append((audio.copy(), sample_rate, offset_samples))
        return [{"transcription": next(self.transcripts), "boundaries": (0, 1)}]


def test_partial_punctuation_revision_does_not_duplicate_words():
    partials = []
    scheduler = LiveAsrScheduler(
        ScriptedBackend(
            "Привет, привет. Как у тебя?",
            "Привет, привет. Как у тебя?",
            "Привет, привет. Как у тебя дела?",
            "Привет, привет! Как у тебя дела? Чем занимаешься?",
        ),
        partial_delay_seconds=0.1,
        on_partial=partials.append,
    )
    try:
        for index in range(4):
            scheduler.submit(voiced_chunk(index * 24_000, 1.5))
            wait_until(lambda index=index: len(partials) == index + 1)

        assert [event.text for event in partials[2:]] == [
            "Привет, привет. Как у тебя дела?",
            "Привет, привет! Как у тебя дела? Чем занимаешься?",
        ]
    finally:
        scheduler.close()


def test_sliding_partial_window_keeps_the_words_that_scrolled_out():
    partials = []
    scheduler = LiveAsrScheduler(
        ScriptedBackend(
            "Как у тебя дела? Чем занимаешься? Что делаешь? И так далее.",
            "занимаешься? Что делаешь? И так далее. Ля-ля-ля.",
        ),
        partial_delay_seconds=0.1,
        partial_context_seconds=12,
        on_partial=partials.append,
    )
    try:
        scheduler.submit(voiced_chunk(0, 12))
        wait_until(lambda: len(partials) == 1)
        scheduler.submit(voiced_chunk(12 * 16_000, 2))
        wait_until(lambda: len(partials) == 2)

        assert partials[-1].text == "Как у тебя дела? Чем занимаешься? Что делаешь? И так далее. Ля-ля-ля."
    finally:
        scheduler.close()


def test_sliding_partial_window_ignores_bare_dashes_when_splicing():
    """Replay of a real session: a «—» token used to splice the window onto the wrong word."""
    partials = []
    scheduler = LiveAsrScheduler(
        ScriptedBackend(
            "Что делаешь? Привет, привет. Как у тебя дела? Чем занимаешься? Что делаешь?",
            "Что делаешь? — Привет, привет! Как у тебя дела? Чем занимаешься? Что делаешь? Итак",
        ),
        partial_delay_seconds=0.1,
        partial_context_seconds=12,
        on_partial=partials.append,
    )
    try:
        scheduler.submit(voiced_chunk(0, 12))
        wait_until(lambda: len(partials) == 1)
        scheduler.submit(voiced_chunk(12 * 16_000, 2))
        wait_until(lambda: len(partials) == 2)

        assert partials[-1].text == (
            "Что делаешь? — Привет, привет! Как у тебя дела? Чем занимаешься? Что делаешь? Итак"
        )
    finally:
        scheduler.close()
