"""Тесты поиска ffmpeg/ffprobe для portable-сборок."""

import os

from src.utils import audio_converter


def test_find_ffmpeg_ignores_non_windows_bundled_binary_on_windows(monkeypatch):
    monkeypatch.setattr(audio_converter, "_project_root", lambda: r"C:\\App")
    monkeypatch.setattr(audio_converter.os, "name", "nt", raising=False)
    monkeypatch.setattr(audio_converter.os.path, "isfile", lambda path: path.endswith("/bin/ffmpeg"))
    monkeypatch.setattr(audio_converter.shutil, "which", lambda name: r"C:\ffmpeg\bin\ffmpeg.exe" if name == "ffmpeg" else None)

    assert audio_converter._find_ffmpeg() == r"C:\ffmpeg\bin\ffmpeg.exe"


def test_find_ffprobe_returns_none_when_only_wrong_bundled_binary_exists_on_windows(monkeypatch):
    monkeypatch.setattr(audio_converter, "_project_root", lambda: r"C:\\App")
    monkeypatch.setattr(audio_converter.os, "name", "nt", raising=False)
    monkeypatch.setattr(audio_converter.os.path, "isfile", lambda path: path.endswith("/bin/ffprobe"))
    monkeypatch.setattr(audio_converter.shutil, "which", lambda name: None)

    assert audio_converter._find_ffprobe() is None


def test_find_ffmpeg_uses_bundled_binary_on_posix(monkeypatch):
    monkeypatch.setattr(audio_converter, "_project_root", lambda: "/opt/app")
    monkeypatch.setattr(audio_converter.os, "name", os.name if os.name != "nt" else "posix", raising=False)
    monkeypatch.setattr(audio_converter.os.path, "isfile", lambda path: path == "/opt/app/bin/ffmpeg")
    monkeypatch.setattr(audio_converter.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    assert audio_converter._find_ffmpeg() == "/opt/app/bin/ffmpeg"
