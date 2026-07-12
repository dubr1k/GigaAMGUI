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
