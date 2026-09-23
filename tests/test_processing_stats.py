"""Тесты статистики обработки: персистентность и восстановление (Phase 0.8 / 4.1)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("absolute_override", [False, True], ids=["relative", "absolute"])
def test_configured_stats_do_not_modify_application_bundle(tmp_path, absolute_override):
    bundle_dir = tmp_path / "GigaAMTranscriber.app" / "Contents" / "MacOS"
    bundle_dir.mkdir(parents=True)
    config_dir = tmp_path / "user-config"
    stats_file = tmp_path / "custom" / "stats.json" if absolute_override else config_dir / "processing_stats.json"
    env = {
        **os.environ,
        "GIGAAM_CONFIG_DIR": str(config_dir),
        "GIGAAM_DATA_DIR": str(tmp_path / "data"),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    }
    env["STATS_FILE"] = str(stats_file) if absolute_override else "processing_stats.json"
    subprocess.run(
        [
            sys.executable, "-c",
            "from src.config import STATS_FILE; "
            "from src.utils.processing_stats import ProcessingStats; "
            "ProcessingStats(STATS_FILE).add_processing_record('speech.wav', 1024, 1.0)",
        ],
        cwd=bundle_dir, env=env, check=True, capture_output=True, text=True,
    )
    assert not (bundle_dir / "processing_stats.json").exists()
    saved = json.loads(stats_file.read_text(encoding="utf-8"))
    assert saved["history"][0]["file_name"] == "speech.wav"
