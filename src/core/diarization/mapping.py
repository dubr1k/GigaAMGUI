"""Backend-independent mapping of timed ASR words to speaker turns."""

from __future__ import annotations

import math

from .base import SpeakerSegment

UNKNOWN_SPEAKER = "Неизвестный спикер"
MAX_SPEAKER_SNAP_DISTANCE_SEC = 2.0
MIN_SPEAKER_TURN_SEC = 0.4
TIMELINE_EPSILON_SEC = 1e-6


def _validated_timed_words(trans_seg: dict) -> list[dict]:
    """Return a complete valid word timeline or an empty list for segment fallback."""

    raw_words = trans_seg.get("words")
    if not isinstance(raw_words, list) or not raw_words:
        return []
    validated: list[dict] = []
    previous_end = -math.inf
    for word in raw_words:
        if not isinstance(word, dict):
            return []
        raw_text = word.get("text")
        if not isinstance(raw_text, str) or not raw_text.strip():
            return []
        try:
            start = float(word["start"])
            end = float(word["end"])
        except (KeyError, TypeError, ValueError):
            return []
        if (
            not math.isfinite(start)
            or not math.isfinite(end)
            or end <= start
            or start < previous_end
        ):
            return []
        validated.append({"text": raw_text.strip(), "start": start, "end": end})
        previous_end = end
    return validated


class SpeakerMappingMixin:
    def _rename_speakers(self, segments: list[SpeakerSegment]) -> list[SpeakerSegment]:
        order = list(dict.fromkeys(segment.speaker for segment in segments))
        names = {name: f"Спикер №{index + 1}" for index, name in enumerate(order)}
        for segment in segments:
            segment.speaker = names.get(segment.speaker, segment.speaker)
        return segments

    def map_speakers_to_transcription(
        self,
        transcription_segments: list,
        speaker_segments: list[SpeakerSegment],
    ) -> list:
        mapped = []
        for trans_seg in transcription_segments:
            words = self._resolve_word_speakers(trans_seg, speaker_segments)
            if not words:
                turns = [self._map_segment_without_words(trans_seg, speaker_segments)]
                self._append_mapped_turns(mapped, turns)
                continue
            turns = self._group_words_into_turns(self._smooth_micro_turns(words))
            self._append_mapped_turns(mapped, turns)
        return mapped

    @staticmethod
    def _append_mapped_turns(mapped, turns):
        """Добавить реплики, сохраняя строгий хронологический контракт."""

        for turn in turns:
            start, end = turn["boundaries"]
            start = float(start)
            end = float(end)
            if end < start - TIMELINE_EPSILON_SEC:
                raise ValueError("Invalid diarization timeline: turn end precedes start")

            if mapped:
                previous_start, previous_end = mapped[-1]["boundaries"]
                previous_end = float(previous_end)
                if start < previous_end - TIMELINE_EPSILON_SEC:
                    raise ValueError("Diarization output overlap between adjacent ASR chunks")
                if (
                    abs(start - previous_end) <= TIMELINE_EPSILON_SEC
                    and mapped[-1]["speaker"] == turn["speaker"]
                ):
                    mapped[-1]["transcription"] += f' {turn["transcription"]}'
                    mapped[-1]["boundaries"] = (
                        float(previous_start),
                        max(previous_end, end),
                    )
                    previous_words = mapped[-1].get("words")
                    turn_words = turn.get("words")
                    if previous_words is not None and turn_words is not None:
                        previous_words.extend(turn_words)
                    else:
                        mapped[-1].pop("words", None)
                    continue

            mapped.append(turn)

    def _map_segment_without_words(self, trans_seg, speaker_segments):
        segment = dict(trans_seg)
        segment.pop("words", None)
        start, end = segment.get("boundaries", (0.0, 0.0))
        speaker = self._find_speaker_at_time((start + end) / 2, speaker_segments)
        if speaker is None:
            speaker = self._find_speaker_by_overlap(start, end, speaker_segments)
        segment["speaker"] = speaker or UNKNOWN_SPEAKER
        return segment

    def _resolve_word_speakers(self, trans_seg, speaker_segments):
        resolved = []
        for word in _validated_timed_words(trans_seg):
            text = word["text"]
            start = word["start"]
            end = word["end"]
            resolved.append({
                "text": text,
                "start": start,
                "end": end,
                "speaker": self._find_speaker_for_word(start, end, speaker_segments),
            })
        return resolved

    def _find_speaker_for_word(self, start, end, speaker_segments):
        speaker = self._find_speaker_by_overlap(start, end, speaker_segments)
        if speaker is None:
            speaker = self._find_speaker_at_time((start + end) / 2, speaker_segments)
        if speaker is None:
            speaker = self._find_nearest_speaker(start, end, speaker_segments)
        return speaker or UNKNOWN_SPEAKER

    @staticmethod
    def _smooth_micro_turns(words):
        for index in range(1, len(words) - 1):
            previous, current, following = words[index - 1], words[index], words[index + 1]
            if (
                previous["speaker"] == following["speaker"]
                and current["speaker"] != previous["speaker"]
                and current["end"] - current["start"] < MIN_SPEAKER_TURN_SEC
            ):
                current["speaker"] = previous["speaker"]
        return words

    @staticmethod
    def _group_words_into_turns(words):
        turns = []
        for word in words:
            timed_word = {
                "text": word["text"],
                "start": word["start"],
                "end": word["end"],
            }
            if turns and turns[-1]["speaker"] == word["speaker"]:
                turns[-1]["transcription"] += f' {word["text"]}'
                turns[-1]["boundaries"] = (turns[-1]["boundaries"][0], word["end"])
                turns[-1]["words"].append(timed_word)
            else:
                turns.append({
                    "transcription": word["text"],
                    "boundaries": (word["start"], word["end"]),
                    "speaker": word["speaker"],
                    "words": [timed_word],
                })
        return turns

    @staticmethod
    def _find_speaker_at_time(time, speaker_segments):
        for segment in speaker_segments:
            if segment.start <= time <= segment.end:
                return segment.speaker
        return None

    @staticmethod
    def _find_nearest_speaker(
        start,
        end,
        speaker_segments,
        max_distance=MAX_SPEAKER_SNAP_DISTANCE_SEC,
    ):
        best_speaker = None
        best_distance = max_distance
        for segment in speaker_segments:
            distance = max(segment.start - end, start - segment.end, 0.0)
            if distance < best_distance:
                best_distance = distance
                best_speaker = segment.speaker
        return best_speaker

    @staticmethod
    def _find_speaker_by_overlap(start, end, speaker_segments):
        max_overlap = 0.0
        best_speaker = None
        for segment in speaker_segments:
            overlap = max(0.0, min(end, segment.end) - max(start, segment.start))
            if overlap > max_overlap:
                max_overlap = overlap
                best_speaker = segment.speaker
        return best_speaker
