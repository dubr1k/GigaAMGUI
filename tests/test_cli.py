import json

import cli


def test_cli_stats_manager_writes_to_configured_path(tmp_path, monkeypatch):
    stats_file = tmp_path / "writable results" / "processing_stats.json"
    monkeypatch.setattr(cli, "STATS_FILE", str(stats_file), raising=False)

    stats = cli._create_stats_manager()
    stats.add_processing_record(
        file_path="fixture.wav",
        file_size=1024,
        duration=10.0,
        conversion_time=1.0,
        transcription_time=2.0,
    )

    payload = json.loads(stats_file.read_text(encoding="utf-8"))
    assert payload["history"][0]["file_name"] == "fixture.wav"


def test_results_table_shows_why_a_file_failed():
    """Причину провала процессор кладёт в result['error']; таблица CLI показывала
    только «✗ Ошибка», а подробности оставались в файле журнала."""
    from rich.console import Console

    from src.cli_support.ui import display_results

    console = Console(record=True, width=200)
    display_results(console, [
        {"file_path": "/in/ok.wav", "success": True, "error": None, "total_time": 1.0, "media_duration": 5.0},
        {"file_path": "/in/broken.mp4", "success": False, "total_time": 0.2, "media_duration": 0,
         "error": "FFmpeg не смог подготовить звук [код 1]\nподробности ниже"},
        {"file_path": "/in/silent.wav", "success": False, "total_time": 0.1, "media_duration": 0},
    ])

    text = console.export_text()
    # Только первая строка; квадратные скобки — текст, а не разметка rich
    assert "broken.mp4: FFmpeg не смог подготовить звук [код 1]" in text
    assert "подробности ниже" not in text
    assert "ok.wav:" not in text and "silent.wav:" not in text
