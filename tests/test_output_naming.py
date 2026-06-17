"""Тесты единого модуля имён выходных файлов (Phase 1.5)."""

import pytest

from src.utils.output_naming import output_filename, output_path, find_result_file


@pytest.mark.parametrize("fmt,expected", [
    ("txt", "интервью.txt"),
    ("txt_timecodes", "интервью_timecodes.txt"),
    ("txt_diarize", "интервью_diarize.txt"),
    ("txt_diarize_timecodes", "интервью_diarize_timecodes.txt"),
    ("md", "интервью.md"),
    ("srt", "интервью.srt"),
    ("vtt", "интервью.vtt"),
])
def test_output_filename(fmt, expected):
    assert output_filename("интервью", fmt) == expected


def test_output_filename_rejects_unknown():
    with pytest.raises(ValueError):
        output_filename("a", "docx")


def test_find_result_file(tmp_path):
    (tmp_path / "audio.txt").write_text("plain", encoding="utf-8")
    (tmp_path / "audio_timecodes.txt").write_text("ts", encoding="utf-8")

    assert find_result_file(tmp_path, "audio", "txt").name == "audio.txt"
    assert find_result_file(tmp_path, "audio", "txt_timecodes").name == "audio_timecodes.txt"
    assert find_result_file(tmp_path, "audio", "md") is None
    # base, оканчивающийся на _timecodes, не путается с timecodes-файлом
    assert find_result_file(tmp_path, "audio_timecodes", "txt").name == "audio_timecodes.txt"


def test_output_path_join(tmp_path):
    assert output_path(tmp_path, "x", "srt") == str(tmp_path / "x.srt")
