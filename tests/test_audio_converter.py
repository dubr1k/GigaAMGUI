"""Тесты поиска ffmpeg/ffprobe для portable-сборок."""

import os

from src.utils import audio_converter


def test_find_ffmpeg_ignores_non_windows_bundled_binary_on_windows(monkeypatch):
    monkeypatch.setattr(audio_converter, "_ffmpeg_cached", None, raising=False)
    monkeypatch.setattr(audio_converter, "_project_root", lambda: r"C:\\App")
    monkeypatch.setattr(audio_converter.os, "name", "nt", raising=False)
    monkeypatch.setattr(audio_converter.os.path, "isfile", lambda path: path.endswith("/bin/ffmpeg"))
    monkeypatch.setattr(audio_converter.shutil, "which", lambda name: r"C:\ffmpeg\bin\ffmpeg.exe" if name == "ffmpeg" else None)
    monkeypatch.setattr(audio_converter, "_tool_executable", lambda path: True)

    assert audio_converter._find_ffmpeg() == r"C:\ffmpeg\bin\ffmpeg.exe"


def test_find_ffmpeg_falls_back_when_bundled_binary_not_executable(monkeypatch):
    """Битый/чужой по архитектуре bundled ffmpeg (WinError 193) отбрасывается → системный."""
    monkeypatch.setattr(audio_converter, "_ffmpeg_cached", None, raising=False)
    monkeypatch.setattr(audio_converter, "_find_bundled_tool", lambda name: "/opt/app/bin/ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr(audio_converter.shutil, "which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)
    # bundled бинарь не запускается, системный — ок
    monkeypatch.setattr(audio_converter, "_tool_executable", lambda path: path == "/usr/bin/ffmpeg")

    assert audio_converter._find_ffmpeg() == "/usr/bin/ffmpeg"


def test_find_ffprobe_returns_none_when_only_wrong_bundled_binary_exists_on_windows(monkeypatch):
    monkeypatch.setattr(audio_converter, "_project_root", lambda: r"C:\\App")
    monkeypatch.setattr(audio_converter.os, "name", "nt", raising=False)
    monkeypatch.setattr(audio_converter.os.path, "isfile", lambda path: path.endswith("/bin/ffprobe"))
    monkeypatch.setattr(audio_converter.shutil, "which", lambda name: None)

    assert audio_converter._find_ffprobe() is None


def test_find_ffmpeg_uses_bundled_binary_on_posix(monkeypatch):
    monkeypatch.setattr(audio_converter, "_ffmpeg_cached", None, raising=False)
    monkeypatch.setattr(audio_converter, "_project_root", lambda: "/opt/app")
    monkeypatch.setattr(audio_converter.os, "name", os.name if os.name != "nt" else "posix", raising=False)
    monkeypatch.setattr(audio_converter.os.path, "isfile", lambda path: path == "/opt/app/bin/ffmpeg")
    monkeypatch.setattr(audio_converter.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(audio_converter, "_tool_executable", lambda path: True)

    assert audio_converter._find_ffmpeg() == "/opt/app/bin/ffmpeg"


def test_convert_to_wav_reports_monotonic_progress_from_out_time(monkeypatch):
    progresses: list[float | None] = []

    class DummyProcess:
        def __init__(self):
            self._stdout_lines = iter([
                "out_time_ms=1000000\n",
                "out_time_ms=2500000\n",
                "out_time_ms=2500000\n",
                "out_time_ms=5000000\n",
                "",
            ])
            self.stdout = DummyPipe([
                "out_time_ms=1000000\n",
                "out_time_ms=2500000\n",
                "out_time_ms=2500000\n",
                "out_time_ms=5000000\n",
                "",
            ])
            self.stderr = DummyPipe([])

        def wait(self):
            return 0

    class DummyPipe:
        def __init__(self, lines):
            self._lines = iter(lines)
            self.closed = False

        def readline(self):
            return next(self._lines)

        def close(self):
            self.closed = True

    def fake_popen(_command, **_kwargs):
        return DummyProcess()

    monkeypatch.setattr(audio_converter.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(audio_converter.os.path, "isfile", lambda _path: True)
    monkeypatch.setattr(audio_converter.os.path, "abspath", lambda value: value)
    monkeypatch.setattr(audio_converter.os.path, "expanduser", lambda value: value)
    monkeypatch.setattr(audio_converter.uuid, "uuid4", lambda: type("UUID", (), {"hex": "unit-test"})())
    monkeypatch.setattr(audio_converter, "_find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(audio_converter.subprocess, "Popen", fake_popen)
    converter = audio_converter.AudioConverter(logger=lambda *_args, **_kwargs: None)

    result = converter.convert_to_wav(
        "input.mkv",
        "/tmp",
        progress_callback=lambda value: progresses.append(value),
        media_duration=5.0,
    )

    assert result == "/tmp/temp_unit-test_input.mkv.wav"
    assert progresses == [0.2, 0.5, 1.0]


def test_convert_to_wav_unknown_duration_keeps_indeterminate(monkeypatch):
    progresses: list[float | None] = []

    class DummyProcess:
        def __init__(self):
            self._stdout_lines = iter(["out_time_ms=123000\n", ""])
            self.stdout = DummyPipe(["out_time_ms=123000\n", ""])
            self.stderr = DummyPipe([])

        def wait(self):
            return 0

    class DummyPipe:
        def __init__(self, lines):
            self._lines = iter(lines)
            self.closed = False

        def readline(self):
            return next(self._lines)

        def close(self):
            self.closed = True

    def fake_popen(_command, **_kwargs):
        return DummyProcess()

    monkeypatch.setattr(audio_converter.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(audio_converter.os.path, "isfile", lambda _path: True)
    monkeypatch.setattr(audio_converter.os.path, "abspath", lambda value: value)
    monkeypatch.setattr(audio_converter.os.path, "expanduser", lambda value: value)
    monkeypatch.setattr(audio_converter.uuid, "uuid4", lambda: type("UUID", (), {"hex": "unit-test"})())
    monkeypatch.setattr(audio_converter, "_find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(audio_converter.subprocess, "Popen", fake_popen)
    converter = audio_converter.AudioConverter(logger=lambda *_args, **_kwargs: None)
    converter.convert_to_wav(
        "input.mkv",
        "/tmp",
        progress_callback=lambda value: progresses.append(value),
        media_duration=None,
    )

    assert progresses == [None]


def test_convert_to_wav_failed_ffmpeg_returns_none_and_logs(monkeypatch):
    logged: list[str] = []

    class DummyProcess:
        def __init__(self):
            self._stdout_lines = iter([""])
            self.stdout = DummyPipe([""])
            self.stderr = DummyPipe(["Something wrong\\n"])

        def wait(self):
            return 1

    class DummyPipe:
        def __init__(self, lines):
            self._lines = iter(lines)
            self.closed = False

        def readline(self):
            return next(self._lines)

        def close(self):
            self.closed = True

    def fake_popen(_command, **_kwargs):
        return DummyProcess()

    monkeypatch.setattr(audio_converter.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(audio_converter.os.path, "isfile", lambda _path: True)
    monkeypatch.setattr(audio_converter.os.path, "abspath", lambda value: value)
    monkeypatch.setattr(audio_converter.os.path, "expanduser", lambda value: value)
    monkeypatch.setattr(audio_converter.uuid, "uuid4", lambda: type("UUID", (), {"hex": "unit-test"})())
    def logger(*args):
        logged.append(" ".join(str(x) for x in args))

    monkeypatch.setattr(audio_converter, "_find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(audio_converter.subprocess, "Popen", fake_popen)
    converter = audio_converter.AudioConverter(logger=logger)
    converter.convert_to_wav("input.mkv", "/tmp")

    assert any("Ошибка FFmpeg" in line for line in logged)
