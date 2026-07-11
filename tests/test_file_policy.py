"""Характеризующие тесты file_policy — фиксируют текущее поведение api/web 1:1."""
from src.config import MEDIA_EXTENSIONS, SUPPORTED_FORMATS
from src.services import file_policy


def test_safe_filename_strips_path_and_nulls():
    assert file_policy.safe_filename("../../etc/passwd") == "passwd"
    assert file_policy.safe_filename("a\\b\\c.mp3") == "c.mp3"
    assert file_policy.safe_filename("x\x00y.wav") == "xy.wav"
    assert file_policy.safe_filename("  spaced.mp3  ") == "spaced.mp3"
    assert file_policy.safe_filename("") == "upload"
    assert file_policy.safe_filename(None) == "upload"


def test_is_supported_by_glob_matches_api_behavior():
    globs = "*.mp3 *.wav *.m4a"
    assert file_policy.is_supported_by_glob("song.MP3", globs) is True
    assert file_policy.is_supported_by_glob("clip.wav", globs) is True
    assert file_policy.is_supported_by_glob("doc.txt", globs) is False


def test_is_supported_by_set_matches_web_behavior():
    exts = {".mp3", ".wav", ".m4a"}
    assert file_policy.is_supported_by_set("song.MP3", exts) is True
    assert file_policy.is_supported_by_set("doc.txt", exts) is False


def test_glob_matches_real_api_config():
    # реальные форматы api.py
    assert file_policy.is_supported_by_glob("a.mkv", SUPPORTED_FORMATS[1]) is True
    assert file_policy.is_supported_by_glob("a.pdf", SUPPORTED_FORMATS[1]) is False


def test_set_matches_real_web_config():
    assert file_policy.is_supported_by_set("a.mp4", MEDIA_EXTENSIONS) is True
    assert file_policy.is_supported_by_set("a.pdf", MEDIA_EXTENSIONS) is False
