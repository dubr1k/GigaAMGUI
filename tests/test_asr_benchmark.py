from scripts.benchmark_asr_backends import (
    error_rate,
    find_boundary_duplicates,
    normalize_text,
    quality_record,
)


def test_normalize_text_is_deterministic_for_russian():
    assert normalize_text("  Ёлка, ПРИВЕТ!  ") == "елка привет"


def test_word_and_character_error_rates():
    assert error_rate("раз два три", "раз два", unit="word") == 1 / 3
    assert error_rate("кот", "кит", unit="char") == 1 / 3


def test_boundary_duplicate_detection():
    segments = [
        {"transcription": "раз два", "boundaries": (0, 1)},
        {"transcription": "два три", "boundaries": (0.9, 2)},
    ]
    assert find_boundary_duplicates(segments) == [{"left": 0, "right": 1, "tokens": ["два"]}]


def test_quality_record_has_release_gate_fields():
    record = quality_record(
        backend="onnx",
        model="v3_e2e_rnnt",
        provider="CPUExecutionProvider",
        reference="раз два",
        hypothesis="раз три",
        elapsed_seconds=1.0,
        audio_seconds=10.0,
        peak_rss_bytes=100,
        segments=[],
    )
    assert set(record) == {
        "backend", "model", "provider", "wer", "cer", "elapsed_seconds",
        "audio_seconds", "rtfx", "peak_rss_bytes", "boundary_duplicates",
    }
    assert record["rtfx"] == 10.0
