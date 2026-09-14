"""Тесты единого модуля имён выходных файлов (Phase 1.5)."""

import pytest

from src.utils.output_naming import (
    find_output_collisions,
    find_result_file,
    normalized_output_stem,
    output_filename,
    output_path,
)


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


def test_normalized_output_stem_matches_case_and_unicode_variants():
    assert normalized_output_stem("/a/Café.wav") == normalized_output_stem("/b/CAFE\u0301.mp3")


def test_shared_output_directory_detects_same_stem_collision(tmp_path):
    files = [str(tmp_path / "a" / "same.wav"), str(tmp_path / "b" / "SAME.mp3")]
    assert find_output_collisions(files, tmp_path / "out") == [files]


def test_separate_source_directories_do_not_collide_without_shared_output(tmp_path):
    files = [str(tmp_path / "a" / "same.wav"), str(tmp_path / "b" / "same.mp3")]
    assert find_output_collisions(files, None) == []
