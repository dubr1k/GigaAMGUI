"""Scheduling source chunks onto the combined mix track."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import replace
from time import monotonic

from .timeline import AlignedMixer
from .types import CaptureEvent, CaptureEventKind, CaptureSource, PcmChunk

MAX_MIX_SKEW_NS = 1_000_000_000
MAX_SOURCE_START_DELAY_NS = 5_000_000_000
"""Largest start delay between sources that is taken from their clocks."""
MAX_PENDING_MIX_CHUNKS = 100
# How long a source that has already produced audio may stay quiet before the
# mixer stops waiting for it, and how long to wait for a source that has never
# produced anything at all (an idle WASAPI loopback endpoint, typically).
MIX_SOURCE_IDLE_SECONDS = 0.5
MIX_SOURCE_STARTUP_GRACE_SECONDS = 1.0


class MixCoordinator:
    """Queue each source's aligned chunks and write mixed blocks in time order.

    Recording the mix is best-effort like the source tracks: a write failure
    or sources that drift apart disable it once for the session, and capture,
    source recordings and recognition carry on. Callers serialize access (the
    session lock); `clock` is injectable so idle and start-up grace can be
    tested without sleeping.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        write_mix: Callable[[PcmChunk], None],
        notify: Callable[[CaptureEvent], None],
        log: Callable[[str], None],
        translate: Callable[[str, str], str],
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.enabled = enabled
        self.inputs: dict[CaptureSource, list[PcmChunk]] = {}
        self.stalled_sources: set[CaptureSource] = set()
        self._write_mix_block = write_mix
        self._notify = notify
        self._log = log
        self._translate = translate
        self._clock = clock
        self._offset_origins: dict[CaptureSource, int] = {}
        self._source_origins_ns: dict[CaptureSource, int] = {}
        self._last_input_at: dict[CaptureSource, float] = {}
        self._reported_stalls: set[CaptureSource] = set()
        self._started_at = float("inf")
        self._session_origin_ns: int | None = None
        self._mixer = AlignedMixer(max_skew_seconds=MAX_MIX_SKEW_NS / 1_000_000_000)

    def start(self) -> None:
        self._started_at = self._clock()

    def add(self, chunk: PcmChunk, active_sources: Collection[CaptureSource]) -> None:
        if not self.enabled:
            return
        pending = self.inputs.setdefault(chunk.source, [])
        pending.append(self._normalize_timestamp(chunk))
        self._last_input_at[chunk.source] = self._clock()
        self.stalled_sources.discard(chunk.source)
        if len(pending) > MAX_PENDING_MIX_CHUNKS:
            self._mark_stalled_peers(chunk, active_sources)
        self._write_ready(active_sources)

    def flush(self) -> None:
        if not self.enabled:
            self.inputs.clear()
            return
        pending = [chunk for chunks in self.inputs.values() for chunk in chunks]
        self.inputs.clear()
        for chunk in sorted(pending, key=lambda item: item.timestamp_ns):
            self._write({chunk.source: chunk})
        # The mixer holds back audio whose peer has not caught up yet; at stop
        # there is no peer left to wait for, and dropping it would truncate the
        # tail of the mix track.
        try:
            tail = self._mixer.flush()
        except Exception as exc:
            self._log(f"mix flush failed: {type(exc).__name__}: {exc}")
            return
        if tail is not None and len(tail.frames):
            try:
                self._write_mix_block(tail)
            except Exception as exc:
                self._log(f"mix flush failed: {type(exc).__name__}: {exc}")

    def _normalize_timestamp(self, chunk: PcmChunk) -> PcmChunk:
        """Position a chunk on the mix timeline by its audio, not by wall clock.

        Capture timestamps are arrival times, so they jitter by tens of
        milliseconds even when both streams are perfectly continuous. The
        mixer takes the difference between the two sources at face value, and
        jitter has no sign there — every pair contributed ``abs(jitter)``, so
        even zero-mean jitter inflated the track (issue #50).

        Sample offsets come from ``SourceTimeline``, which has already filled
        gaps and trimmed overlaps, so they advance exactly with the audio. The
        one thing wall clock is still needed for — where each source starts —
        stays where it was: both sources are rebased onto the session origin at
        their first chunk.
        """
        if self._session_origin_ns is None:
            self._session_origin_ns = chunk.timestamp_ns
        if chunk.source not in self._offset_origins:
            # Read the clock once per source, at its first chunk. Rebasing every
            # source onto the session origin instead erased the start delay of
            # a later source (SCStream starts ~0.5 s after the microphone), and
            # the system track played that much early in mix.flac.
            # Devices can also run on unrelated clocks (WASAPI endpoints have
            # their own epochs); a delay outside a plausible start-up window
            # says nothing about when the source started, so it starts at the
            # session origin as before.
            self._offset_origins[chunk.source] = chunk.sample_offset
            delay_ns = chunk.timestamp_ns - self._session_origin_ns
            self._source_origins_ns[chunk.source] = self._session_origin_ns + (
                delay_ns if 0 < delay_ns <= MAX_SOURCE_START_DELAY_NS else 0
            )
        origin_offset = self._offset_origins[chunk.source]
        elapsed_ns = round((chunk.sample_offset - origin_offset) * 1_000_000_000 / chunk.sample_rate)
        return replace(chunk, timestamp_ns=self._source_origins_ns[chunk.source] + elapsed_ns)

    def _participants(self, active_sources: Collection[CaptureSource]) -> set[CaptureSource]:
        """Sources the mixer should still wait for before writing a block.

        A source that never starts (an idle loopback endpoint) or that has gone
        quiet must not hold the mix track hostage — that starvation is what
        used to disable mixed recording seconds after the session began.
        """
        now = self._clock()
        participants: set[CaptureSource] = set()
        for source in active_sources:
            if source in self.stalled_sources:
                continue
            last_input_at = self._last_input_at.get(source)
            if last_input_at is None:
                if now - self._started_at <= MIX_SOURCE_STARTUP_GRACE_SECONDS:
                    participants.add(source)
                continue
            if now - last_input_at <= MIX_SOURCE_IDLE_SECONDS:
                participants.add(source)
        return participants

    def _mark_stalled_peers(self, chunk: PcmChunk, active_sources: Collection[CaptureSource]) -> None:
        stalled = {
            source for source in active_sources
            if source is not chunk.source and not self.inputs.get(source)
        }
        if not stalled:
            return
        self.stalled_sources |= stalled
        for source in sorted(stalled, key=lambda item: item.value):
            self._log(f"mix peer stalled [{source.value}]: no audio delivered; mixing without it")
            if source in self._reported_stalls:
                continue
            self._reported_stalls.add(source)
            self._notify(CaptureEvent(
                CaptureEventKind.STATUS,
                source,
                chunk.sample_offset,
                chunk.timestamp_ns,
                self._translate(
                    f"Источник «{source.value}» не отдаёт звук — проверьте выбранное устройство. "
                    "Общая дорожка записывается без него.",
                    f"Source '{source.value}' is not delivering audio — check the selected device. "
                    "The combined track continues without it.",
                ),
            ))

    def _write_ready(self, active_sources: Collection[CaptureSource]) -> None:
        if not self.enabled:
            return
        while True:
            participants = self._participants(active_sources)
            if not participants or not all(self.inputs.get(source) for source in participants):
                return
            heads = {source: self.inputs[source][0] for source in participants}
            earliest = min(heads.values(), key=lambda chunk: chunk.timestamp_ns)
            latest_timestamp_ns = max(chunk.timestamp_ns for chunk in heads.values())
            if latest_timestamp_ns - earliest.timestamp_ns > MAX_MIX_SKEW_NS:
                self._disable(
                    earliest.source,
                    earliest.timestamp_ns,
                    "timestamp skew exceeds 1.000s",
                )
                return
            # Mix in time order: a head that starts after the earliest one ends
            # waits for its turn. Popping one chunk from every source paired
            # chunks by index, so a source that started later stayed a constant
            # distance ahead of its peer instead of lining up with it.
            earliest_end_ns = earliest.timestamp_ns + round(
                len(earliest.frames) * 1_000_000_000 / earliest.sample_rate
            )
            inputs = {
                source: self.inputs[source].pop(0)
                for source, head in heads.items()
                if head.timestamp_ns < earliest_end_ns
            }
            self._write(inputs)

    def _write(self, chunks: Mapping[CaptureSource, PcmChunk]) -> None:
        try:
            self._write_mix_block(self._mixer.mix(chunks))
        except Exception as exc:
            timestamp_ns = min(chunk.timestamp_ns for chunk in chunks.values())
            self._disable(next(iter(chunks)), timestamp_ns, str(exc))

    def _disable(self, source: CaptureSource, timestamp_ns: int, reason: str) -> None:
        if not self.enabled:
            return
        self.enabled = False
        self.inputs.clear()
        self._log(f"mix recording disabled [{source.value}]: {reason}")
        detail = self._translate(
            "Запись смешанного аудио отключена для этой сессии: "
            f"{reason}. Раздельная запись микрофона и системы, а также распознавание продолжаются.",
            "Mixed audio recording disabled for this session: "
            f"{reason}. Separate microphone and system recording and recognition continue.",
        )
        self._notify(CaptureEvent(CaptureEventKind.STATUS, source, 0, timestamp_ns, detail))
