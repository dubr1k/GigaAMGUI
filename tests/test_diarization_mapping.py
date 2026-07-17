"""Тесты чистой логики диаризации без загрузки моделей (Phase 4.1)."""

import pytest

from src.utils.diarization import (
    DiarizationManager,
    SortformerDiarizationManager,
    SpeakerSegment,
)


def _mgr():
    # Пайплайн ленивый — инстанс без токена безопасен для проверки маппинга
    return DiarizationManager(hf_token="hf_dummy", device="cpu")


def _sortformer_mgr():
    # NeMo импортируется только при обращении к pipeline — маппинг проверяем без него
    return SortformerDiarizationManager(device="cpu")


# Оба backend-а наследуют один слой сопоставления, поэтому поведение проверяем на обоих
_MANAGER_FACTORIES = pytest.mark.parametrize(
    "make_manager",
    [_mgr, _sortformer_mgr],
    ids=["pyannote", "sortformer"],
)


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


@_MANAGER_FACTORIES
def test_word_in_speaker_pause_snaps_to_nearest_speaker(make_manager):
    """Regression issue #27: однофреймовое слово в паузе не должно рвать реплику.

    GigaAM RNNT отдаёт однотокенному слову ровно один энкодерный фрейм (~0.08 с).
    Такое слово целиком проваливается в паузу между сегментами диаризации и
    раньше получало «Неизвестный спикер», разрывая монолог одного человека.
    """
    mgr = make_manager()
    speaker_segs = [
        SpeakerSegment(240.0, 248.10, "Спикер №4"),
        SpeakerSegment(248.30, 261.0, "Спикер №4"),
    ]
    trans = [{
        "transcription": "Но фестиваль был нужен вам, людям.",
        "boundaries": (248.15, 249.2),
        "words": [
            {"text": "Но", "start": 248.15, "end": 248.23},
            {"text": "фестиваль", "start": 248.40, "end": 248.90},
            {"text": "был", "start": 249.0, "end": 249.2},
        ],
    }]

    mapped = mgr.map_speakers_to_transcription(trans, speaker_segs)

    assert mapped == [{
        "transcription": "Но фестиваль был",
        "boundaries": (248.15, 249.2),
        "speaker": "Спикер №4",
    }]


@_MANAGER_FACTORIES
def test_word_far_from_any_speech_stays_unknown(make_manager):
    """Снап работает только рядом с речью — иначе «Неизвестный спикер» осмыслен."""
    mgr = make_manager()
    speaker_segs = [SpeakerSegment(0.0, 1.0, "A")]
    trans = [{
        "transcription": "что-то",
        "boundaries": (50.0, 50.5),
        "words": [{"text": "что-то", "start": 50.0, "end": 50.5}],
    }]

    mapped = mgr.map_speakers_to_transcription(trans, speaker_segs)

    assert mapped[0]["speaker"] == "Неизвестный спикер"


@_MANAGER_FACTORIES
def test_single_short_word_does_not_flip_speaker_between_same_neighbours(make_manager):
    """Микро-реплика в 80 мс между двумя репликами одного спикера — артефакт.

    Слово может задеть краем чужой сегмент; смена говорящего на одно короткое
    слово внутри монолога физически неправдоподобна и поглощается соседями.
    """
    mgr = make_manager()
    speaker_segs = [
        SpeakerSegment(0.0, 2.0, "A"),
        SpeakerSegment(2.0, 2.10, "B"),
        SpeakerSegment(2.10, 6.0, "A"),
    ]
    trans = [{
        "transcription": "мы говорим и показываем",
        "boundaries": (0.5, 5.0),
        "words": [
            {"text": "мы", "start": 0.5, "end": 1.0},
            {"text": "говорим", "start": 1.2, "end": 1.9},
            {"text": "и", "start": 2.02, "end": 2.08},
            {"text": "показываем", "start": 3.0, "end": 4.0},
        ],
    }]

    mapped = mgr.map_speakers_to_transcription(trans, speaker_segs)

    assert mapped == [{
        "transcription": "мы говорим и показываем",
        "boundaries": (0.5, 4.0),
        "speaker": "A",
    }]


@_MANAGER_FACTORIES
def test_real_speaker_turn_survives_smoothing(make_manager):
    """Сглаживание не должно съедать настоящую смену говорящего."""
    mgr = make_manager()
    speaker_segs = [
        SpeakerSegment(0.0, 2.0, "A"),
        SpeakerSegment(2.0, 4.0, "B"),
        SpeakerSegment(4.0, 6.0, "A"),
    ]
    trans = [{
        "transcription": "вопрос ответ развёрнутый продолжение",
        "boundaries": (0.5, 5.0),
        "words": [
            {"text": "вопрос", "start": 0.5, "end": 1.5},
            {"text": "ответ", "start": 2.2, "end": 2.9},
            {"text": "развёрнутый", "start": 3.0, "end": 3.8},
            {"text": "продолжение", "start": 4.2, "end": 5.0},
        ],
    }]

    mapped = mgr.map_speakers_to_transcription(trans, speaker_segs)

    assert [seg["speaker"] for seg in mapped] == ["A", "B", "A"]
