"""Тесты статистики обработки: персистентность и восстановление (Phase 0.8 / 4.1)."""

from src.utils.processing_stats import ProcessingStats


def test_record_roundtrip(tmp_path):
    f = str(tmp_path / "stats.json")
    s = ProcessingStats(stats_file=f)
    s.add_processing_record("audio.mp3", file_size=1024 * 1024, duration=60.0,
                            conversion_time=5.0, transcription_time=25.0, success=True)
    # Перечитываем с диска новым экземпляром
    s2 = ProcessingStats(stats_file=f)
    assert len(s2.stats["history"]) == 1
    assert ".mp3" in s2.stats["summary"]


def test_corrupt_file_recovers_to_default(tmp_path):
    f = tmp_path / "stats.json"
    f.write_text("{ broken json", encoding="utf-8")
    s = ProcessingStats(stats_file=str(f))
    assert s.stats == {"history": [], "summary": {}}


def test_estimate_uses_history(tmp_path):
    f = str(tmp_path / "stats.json")
    s = ProcessingStats(stats_file=f)
    s.add_processing_record("a.mp3", 1024 * 1024, duration=100.0,
                            conversion_time=10.0, transcription_time=40.0, success=True)
    est = s.estimate_processing_time("b.mp3", media_duration=100.0)
    assert est > 0
