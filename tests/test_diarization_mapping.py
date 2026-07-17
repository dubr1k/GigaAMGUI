"""Тесты чистой логики диаризации без загрузки моделей (Phase 4.1)."""

from src.utils.diarization import DiarizationManager, SpeakerSegment


def _mgr():
    # Пайплайн ленивый — инстанс без токена безопасен для проверки маппинга
    return DiarizationManager(hf_token="hf_dummy", device="cpu")


def test_rename_speakers_in_order_of_appearance():
    mgr = _mgr()
    segs = [
        SpeakerSegment(0.0, 1.0, "SPEAKER_01"),
        SpeakerSegment(1.0, 2.0, "SPEAKER_00"),
        SpeakerSegment(2.0, 3.0, "SPEAKER_01"),
    ]
    renamed = mgr._rename_speakers(segs)
    # SPEAKER_01 появился первым -> Спикер №1
    assert renamed[0].speaker == "Спикер №1"
    assert renamed[1].speaker == "Спикер №2"
    assert renamed[2].speaker == "Спикер №1"


def test_find_speaker_at_time():
    mgr = _mgr()
    segs = [SpeakerSegment(0.0, 2.0, "A"), SpeakerSegment(2.0, 4.0, "B")]
    assert mgr._find_speaker_at_time(1.0, segs) == "A"
    assert mgr._find_speaker_at_time(3.0, segs) == "B"
    assert mgr._find_speaker_at_time(10.0, segs) is None


def test_map_speakers_to_transcription_uses_midpoint():
    mgr = _mgr()
    speaker_segs = [SpeakerSegment(0.0, 5.0, "A"), SpeakerSegment(5.0, 10.0, "B")]
    trans = [
        {"transcription": "привет", "boundaries": (0.0, 4.0)},   # midpoint 2.0 -> A
        {"transcription": "пока", "boundaries": (6.0, 9.0)},     # midpoint 7.5 -> B
    ]
    mapped = mgr.map_speakers_to_transcription(trans, speaker_segs)
    assert mapped[0]["speaker"] == "A"
    assert mapped[1]["speaker"] == "B"


def test_map_speakers_unknown_when_no_overlap():
    mgr = _mgr()
    speaker_segs = [SpeakerSegment(0.0, 1.0, "A")]
    trans = [{"transcription": "x", "boundaries": (50.0, 60.0)}]
    mapped = mgr.map_speakers_to_transcription(trans, speaker_segs)
    assert mapped[0]["speaker"] == "Неизвестный спикер"


def test_map_speakers_splits_word_timed_segment_at_speaker_changes():
    mgr = _mgr()
    speaker_segs = [
        SpeakerSegment(0.0, 1.8, "A"),
        SpeakerSegment(1.8, 4.0, "B"),
        SpeakerSegment(4.0, 6.0, "A"),
    ]
    trans = [{
        "transcription": "Алло. Здравствуйте! Да, слушаю.",
        "boundaries": (0.0, 6.0),
        "words": [
            {"text": "Алло.", "start": 0.2, "end": 1.0},
            {"text": "Здравствуйте!", "start": 2.0, "end": 3.4},
            {"text": "Да,", "start": 4.2, "end": 4.6},
            {"text": "слушаю.", "start": 4.7, "end": 5.5},
        ],
    }]

    mapped = mgr.map_speakers_to_transcription(trans, speaker_segs)

    assert mapped == [
        {"transcription": "Алло.", "boundaries": (0.2, 1.0), "speaker": "A"},
        {"transcription": "Здравствуйте!", "boundaries": (2.0, 3.4), "speaker": "B"},
        {"transcription": "Да, слушаю.", "boundaries": (4.2, 5.5), "speaker": "A"},
    ]
