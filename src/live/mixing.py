"""Scheduling source chunks onto the combined mix track."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import replace
from time import monotonic

from .reporting import ReportOnce
from .timeline import AlignedMixer, SessionTimebase
from .types import CaptureEvent, CaptureEventKind, CaptureSource, PcmChunk

MAX_MIX_SKEW_NS = 1_000_000_000
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
        timebase: SessionTimebase | None = None,
    ) -> None:
        self.enabled = enabled
        self.inputs: dict[CaptureSource, list[PcmChunk]] = {}
        self.stalled_sources: set[CaptureSource] = set()
        self._write_mix_block = write_mix
        self._notify = notify
        self._log = log
        self._translate = translate
        self._clock = clock
        self._timebase = timebase or SessionTimebase(clock)
        self._last_input_at: dict[CaptureSource, float] = {}
        self._reported_stalls = ReportOnce()
        self._started_at = float("inf")
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
        gaps, trimmed overlaps and stepped over the time a source was silent
        without delivering anything, so they advance with the audio. Where
        each source starts on the session timeline is the timebase's call;
        recognition places its chunks with the same timebase.
        """
        position_ns = self._timebase.position_ns(chunk)
        return replace(chunk, timestamp_ns=(self._timebase.origin_ns or 0) + position_ns)

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
            if not self._reported_stalls.first(source):
                continue
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
