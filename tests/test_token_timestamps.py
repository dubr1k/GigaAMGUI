from src.core.asr.token_timestamps import tokens_to_words


def test_sentencepiece_tokens_become_monotonic_words():
    assert tokens_to_words(
        [" привет", " мир", "!"],
        [0.04, 0.44, 0.72],
        duration=1.0,
    ) == [
        {"text": "привет", "start": 0.04, "end": 0.44},
        {"text": "мир!", "start": 0.44, "end": 0.76},
    ]


def test_underscore_marker_and_continuation_pieces_form_words():
    assert tokens_to_words(
        ["▁провер", "ка", "▁свя", "зи"],
        [0.0, 0.08, 0.24, 0.32],
        duration=0.6,
    ) == [
        {"text": "проверка", "start": 0.0, "end": 0.24},
        {"text": "связи", "start": 0.24, "end": 0.36},
    ]


def test_punctuation_does_not_create_a_separate_word():
    assert tokens_to_words(
        [" тест", ",", " снова", "."],
        [0.1, 0.2, 0.5, 0.8],
        duration=1.0,
    ) == [
        {"text": "тест,", "start": 0.1, "end": 0.5},
        {"text": "снова.", "start": 0.5, "end": 0.84},
    ]


def test_missing_or_mismatched_timestamps_return_none():
    assert tokens_to_words([" тест"], None, duration=1.0) is None
    assert tokens_to_words([" тест"], [], duration=1.0) is None
    assert tokens_to_words([" тест", " два"], [0.1], duration=1.0) is None


def test_timestamps_are_clamped_and_kept_monotonic():
    assert tokens_to_words(
        [" первый", " второй"],
        [-0.2, -0.4],
        duration=0.3,
    ) == [
        {"text": "первый", "start": 0.0, "end": 0.0},
        {"text": "второй", "start": 0.0, "end": 0.04},
    ]


def test_empty_tokens_are_ignored_without_losing_timestamp_alignment():
    assert tokens_to_words(
        [" первый", "", " второй"],
        [0.1, 0.2, 0.4],
        duration=0.8,
    ) == [
        {"text": "первый", "start": 0.1, "end": 0.4},
        {"text": "второй", "start": 0.4, "end": 0.44},
    ]


def test_ctc_character_tokens_use_standalone_spaces_as_word_boundaries():
    assert tokens_to_words(
        ["в", "с", "е", " ", "т", "у", "т"],
        [0.1, 0.14, 0.18, 0.22, 0.3, 0.34, 0.38],
        duration=0.6,
    ) == [
        {"text": "все", "start": 0.1, "end": 0.3},
        {"text": "тут", "start": 0.3, "end": 0.42},
    ]
