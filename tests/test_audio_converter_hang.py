from threading import Event

from src.utils import audio_converter


class _EOFPipe:
    def readline(self):
        return ""


class _CleanProc:
    """ffmpeg, который выдал пару progress-строк и корректно завершился."""
    def __init__(self):
        self._lines = iter(["out_time_ms=1000000\n", "progress=end\n", ""])
        self.stdout = self
        self.stderr = _EOFPipe()

    def readline(self):
        return next(self._lines)

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


class _HangingProc:
    """ffmpeg, который не выдаёт вывод и не завершается, пока его не убьют."""
    def __init__(self):
        self._killed = Event()
        self.stdout = self
        self.stderr = _EOFPipe()

    def readline(self):
        self._killed.wait()  # блокируемся до kill, потом EOF — активности нет
        return ""

    def wait(self, timeout=None):
        self._killed.wait()
        return -9

    def kill(self):
        self._killed.set()


def test_convert_passes_utf8_replace_to_popen(monkeypatch, tmp_path):
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured.update(kwargs)
        return _CleanProc()

    monkeypatch.setattr(audio_converter, "_find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(audio_converter.subprocess, "Popen", fake_popen)

    src = tmp_path / "in.mp4"
    src.write_bytes(b"\x00" * 16)
    conv = audio_converter.AudioConverter(logger=lambda *a, **k: None)
    out = conv.convert_to_wav(str(src), str(tmp_path), media_duration=10.0)

    assert out is not None  # happy path still works
    assert captured.get("encoding") == "utf-8"
    assert captured.get("errors") == "replace"


def test_convert_times_out_instead_of_hanging(monkeypatch, tmp_path):
    monkeypatch.setattr(audio_converter, "_CONVERSION_STALL_TIMEOUT", 0.3)
    monkeypatch.setattr(audio_converter, "_find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(audio_converter.subprocess, "Popen", lambda cmd, **k: _HangingProc())

    src = tmp_path / "in.mp4"
    src.write_bytes(b"\x00" * 16)
    conv = audio_converter.AudioConverter(logger=lambda *a, **k: None)
    out = conv.convert_to_wav(str(src), str(tmp_path), media_duration=0.0)

    assert out is None  # watchdog убил зависший ffmpeg, а не завис навсегда


class _RunningProc:
    """ffmpeg, который пишет частичный WAV и работает, пока его не убьют."""

    def __init__(self, cmd, lines, returncode=0):
        self.output = cmd[cmd.index("-y") + 1]
        with open(self.output, "wb") as handle:
            handle.write(b"RIFF-partial")
        self._lines = iter(lines)
        self._returncode = returncode
        self.killed = False
        self.waited = False
        self.stdout = self
        self.stderr = _EOFPipe()

    def readline(self):
        return next(self._lines, "")

    def poll(self):
        return None if not (self.killed or self.waited) else self._returncode

    def wait(self, timeout=None):
        self.waited = True
        return -9 if self.killed else self._returncode

    def kill(self):
        self.killed = True


def _converter_with(monkeypatch, tmp_path, make_proc):
    procs = []

    def fake_popen(cmd, **_kwargs):
        proc = make_proc(cmd)
        procs.append(proc)
        return proc

    monkeypatch.setattr(audio_converter, "_find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(audio_converter.subprocess, "Popen", fake_popen)
    src = tmp_path / "in.mp4"
    src.write_bytes(b"\x00" * 16)
    out_dir = tmp_path / "tmp"
    out_dir.mkdir()
    return audio_converter.AudioConverter(logger=lambda *a, **k: None), str(src), out_dir, procs


def test_failed_ffmpeg_does_not_leave_partial_wav(monkeypatch, tmp_path):
    conv, src, out_dir, procs = _converter_with(
        monkeypatch, tmp_path, lambda cmd: _RunningProc(cmd, ["progress=end\n"], returncode=1)
    )

    assert conv.convert_to_wav(src, str(out_dir), media_duration=10.0) is None
    assert procs and list(out_dir.iterdir()) == []


def test_progress_callback_error_stops_ffmpeg_and_removes_partial_wav(monkeypatch, tmp_path):
    # Исключение из колбэка внутри цикла чтения оставляло ffmpeg работать до
    # 600-секундного watchdog, а частичный WAV — на диске.
    conv, src, out_dir, procs = _converter_with(
        monkeypatch,
        tmp_path,
        lambda cmd: _RunningProc(cmd, ["out_time_ms=1000000\n", "out_time_ms=2000000\n"]),
    )

    def callback(_ratio):
        raise RuntimeError("клиент отключился")

    import pytest

    with pytest.raises(RuntimeError, match="клиент отключился"):
        conv.convert_to_wav(src, str(out_dir), media_duration=10.0, progress_callback=callback)

    assert procs[0].killed
    assert list(out_dir.iterdir()) == []


def test_watchdog_kill_removes_partial_wav(monkeypatch, tmp_path):
    monkeypatch.setattr(audio_converter, "_CONVERSION_STALL_TIMEOUT", 0.3)

    class Hanging(_RunningProc):
        def __init__(self, cmd):
            super().__init__(cmd, [])
            self._killed_event = Event()

        def readline(self):
            self._killed_event.wait()
            return ""

        def kill(self):
            super().kill()
            self._killed_event.set()

    conv, src, out_dir, procs = _converter_with(monkeypatch, tmp_path, Hanging)

    assert conv.convert_to_wav(src, str(out_dir), media_duration=0.0) is None
    assert list(out_dir.iterdir()) == []
