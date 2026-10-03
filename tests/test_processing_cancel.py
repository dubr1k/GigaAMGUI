"""Отмена посреди файла: ffmpeg, окна ASR и сам процессор.

Раньше процессор не умел прерываться внутри файла: TUI и PyQt могли только
дождаться его конца («закончим текущий файл и остановимся»), а на часовой
записи это десятки минут.
"""

from pathlib import Path

import pytest

from src.core.asr.chunking import AudioChunk
from src.core.asr.longform import assemble_segments
from src.core.processor import TranscriptionProcessor
from src.utils import audio_converter
from src.utils.cancellation import ProcessingCancelled


class _EOFPipe:
    def readline(self):
        return ""


class _ProgressingProc:
    def __init__(self, cmd):
        self.output = cmd[cmd.index("-y") + 1]
        Path(self.output).write_bytes(b"RIFF-partial")
        self._lines = iter([f"out_time_ms={i * 1_000_000}\n" for i in range(1, 100)])
        self.killed = False
        self.stdout = self
        self.stderr = _EOFPipe()

    def readline(self):
        return next(self._lines, "")

    def poll(self):
        return -9 if self.killed else None

    def wait(self, timeout=None):
        return -9 if self.killed else 0

    def kill(self):
        self.killed = True


def test_conversion_cancel_kills_ffmpeg_and_drops_partial_wav(monkeypatch, tmp_path):
    procs = []
    monkeypatch.setattr(audio_converter, "_find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(
        audio_converter.subprocess,
        "Popen",
        lambda cmd, **_kw: procs.append(_ProgressingProc(cmd)) or procs[-1],
    )
    source = tmp_path / "in.mp4"
    source.write_bytes(b"\x00")
    out_dir = tmp_path / "tmp"
    out_dir.mkdir()
    seen = []

    def cancel_after_two_lines():
        seen.append(1)
        return len(seen) > 2

    converter = audio_converter.AudioConverter(logger=lambda *_a: None)
    with pytest.raises(ProcessingCancelled):
        converter.convert_to_wav(
            str(source), str(out_dir), media_duration=100.0, cancel_check=cancel_after_two_lines
        )

    assert procs[0].killed
    assert list(out_dir.iterdir()) == []


def test_window_assembly_stops_between_windows():
    decoded = []
    chunks = [AudioChunk(group=i, decode_start_sample=i * 16000, decode_end_sample=(i + 1) * 16000,
                         start_sec=float(i), end_sec=float(i + 1)) for i in range(5)]

    def decode(chunk):
        decoded.append(chunk.group)
        return "слово", None

    with pytest.raises(ProcessingCancelled):
        assemble_segments(
            chunks,
            decode,
            total_seconds=5.0,
            cancel_check=lambda: len(decoded) >= 2,
        )

    assert decoded == [0, 1]


class _Stats:
    pass


def _processor(monkeypatch, tmp_path, loader):
    source = tmp_path / "in.wav"
    source.write_bytes(b"stub")
    processor = TranscriptionProcessor(loader, _Stats(), logger=lambda _m: None)
    monkeypatch.setattr("src.core.processor.AudioConverter.get_media_duration", lambda _p: 1.0)
    return processor, source


def test_processor_reports_cancel_and_writes_nothing(monkeypatch, tmp_path):
    class Loader:
        def transcribe_longform(self, audio_path, progress_callback=None, cancel_check=None):
            if cancel_check is not None and cancel_check():
                raise ProcessingCancelled()
            return [{"transcription": "x", "boundaries": (0.0, 1.0)}]

    processor, source = _processor(monkeypatch, tmp_path, Loader())
    created = []

    def convert(input_path, output_dir, **_kwargs):
        target = Path(output_dir) / "temp.wav"
        target.write_bytes(b"RIFF")
        created.append(target)
        return str(target)

    monkeypatch.setattr(processor.audio_converter, "convert_to_wav", convert)
    out = tmp_path / "out"

    # Отмена приходит, когда WAV уже создан: он должен исчезнуть вместе с файлом.
    result = processor.process_file(
        str(source), str(out), 0, 1, output_formats=["txt"], cancel_check=lambda: bool(created)
    )

    assert result["success"] is False
    assert result["cancelled"] is True
    assert "отменена" in result["error"]
    assert created and not any(path.exists() for path in created)
    assert list(out.iterdir()) == []


def test_processor_without_cancel_check_keeps_old_loader_signature(monkeypatch, tmp_path):
    class OldLoader:
        def transcribe_longform(self, audio_path, progress_callback=None):
            return [{"transcription": "x", "boundaries": (0.0, 1.0)}]

    processor, source = _processor(monkeypatch, tmp_path, OldLoader())
    monkeypatch.setattr(processor.audio_converter, "convert_to_wav", lambda *a, **k: str(source))

    result = processor.process_file(str(source), str(tmp_path / "out"), 0, 1, cancel_check=lambda: False)

    assert result["success"] is True
    assert result["cancelled"] is False
