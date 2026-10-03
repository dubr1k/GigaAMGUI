"""Временные WAV одной обработки не теряются и не остаются после неё."""

import os
from pathlib import Path

import pytest

from src.core.processor import TranscriptionProcessor
from src.core.progress import ProgressEvent


class _Stats:
    pass


class _Loader:
    def transcribe_longform(self, audio_path, progress_callback=None):
        return [{"transcription": "привет", "boundaries": (0.0, 1.0)}]


class _WritingConverter:
    """Как настоящий конвертер: пишет WAV в переданный каталог."""

    last_error = None

    def __init__(self):
        self.dirs: list[str] = []
        self.created: list[str] = []

    def convert_to_wav(self, input_path, output_dir, progress_callback=None, media_duration=None):
        self.dirs.append(output_dir)
        target = os.path.join(output_dir, "temp_unit.wav")
        Path(target).write_bytes(b"RIFF")
        self.created.append(target)
        return target


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "in.mp3"
    path.write_bytes(b"stub")
    return path


def test_missing_output_directory_is_created(monkeypatch, tmp_path):
    # CLI с новой папкой вывода падал на каждом файле: временный WAV писался
    # в несуществующий каталог, и результаты сохранить было некуда.
    source = _source(tmp_path)
    output_dir = tmp_path / "new" / "results"
    converter = _WritingConverter()
    processor = TranscriptionProcessor(_Loader(), _Stats(), logger=lambda _m: None)
    processor.audio_converter = converter
    monkeypatch.setattr("src.core.processor.AudioConverter.get_media_duration", lambda _p: 1.0)

    result = processor.process_file(str(source), str(output_dir), 0, 1, output_formats=["txt"])

    assert result["success"] is True, result["error"]
    assert (output_dir / "in.txt").read_text(encoding="utf-8") == "привет"
    assert converter.dirs and all(os.path.isabs(item) for item in converter.dirs)


def test_temporary_wav_does_not_land_in_output_directory(monkeypatch, tmp_path):
    source = _source(tmp_path)
    output_dir = tmp_path / "out"
    converter = _WritingConverter()
    processor = TranscriptionProcessor(_Loader(), _Stats(), logger=lambda _m: None)
    processor.audio_converter = converter
    monkeypatch.setattr("src.core.processor.AudioConverter.get_media_duration", lambda _p: 1.0)

    processor.process_file(str(source), str(output_dir), 0, 1, output_formats=["txt"])

    assert sorted(path.name for path in output_dir.iterdir()) == ["in.txt"]
    assert all(not os.path.exists(path) for path in converter.created)


def test_temporary_wav_is_removed_when_preprocessing_progress_raises(monkeypatch, tmp_path):
    # Исключение между конвертацией и try/finally распознавания (например,
    # из progress-колбэка на стадии preprocessing) оставляло WAV на диске.
    source = _source(tmp_path)
    converter = _WritingConverter()

    def progress(event: ProgressEvent):
        if event.stage == "preprocessing":
            raise RuntimeError("клиент отключился")

    processor = TranscriptionProcessor(
        _Loader(), _Stats(), logger=lambda _m: None, progress_callback=progress
    )
    processor.audio_converter = converter
    monkeypatch.setattr("src.core.processor.AudioConverter.get_media_duration", lambda _p: 1.0)

    with pytest.raises(RuntimeError, match="клиент отключился"):
        processor.process_file(str(source), str(tmp_path / "out"), 0, 1)

    assert converter.created
    assert all(not os.path.exists(path) for path in converter.created)
    assert all(not os.path.exists(directory) for directory in converter.dirs)


def test_source_file_is_never_deleted_even_if_converter_returns_it(monkeypatch, tmp_path):
    source = tmp_path / "already.wav"
    source.write_bytes(b"RIFF")

    class PassThrough:
        last_error = None

        def convert_to_wav(self, input_path, output_dir, **_kwargs):
            return input_path

    processor = TranscriptionProcessor(_Loader(), _Stats(), logger=lambda _m: None)
    processor.audio_converter = PassThrough()
    monkeypatch.setattr("src.core.processor.AudioConverter.get_media_duration", lambda _p: 1.0)

    processor.process_file(str(source), str(tmp_path), 0, 1)

    assert source.exists()
