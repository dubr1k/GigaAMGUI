"""Сохранение результатов: каждый формат отдельно и атомарно."""

from pathlib import Path

import pytest

from src.core.processor import TranscriptionProcessor


class _Stats:
    pass


class _Loader:
    def transcribe_longform(self, audio_path, progress_callback=None):
        return [{"transcription": "Привет.", "boundaries": (0.0, 1.0)}]


class _Diarization:
    backend = "onnx"
    provider = "auto"
    hf_token = None

    def diarize(self, audio_path, num_speakers=None, progress_callback=None):
        return [object()]

    def map_speakers_to_transcription(self, utterances, speaker_segments):
        return [{**item, "speaker": "Спикер №1"} for item in utterances]


def _processor(monkeypatch, tmp_path, **kwargs):
    source = tmp_path / "in.wav"
    source.write_bytes(b"stub")
    processor = TranscriptionProcessor(_Loader(), _Stats(), logger=lambda _m: None, **kwargs)
    monkeypatch.setattr(processor.audio_converter, "convert_to_wav", lambda *a, **k: str(source))
    monkeypatch.setattr("src.core.processor.AudioConverter.get_media_duration", lambda _p: 1.0)
    return processor, source


def test_one_failing_format_does_not_lose_the_others(monkeypatch, tmp_path):
    # Сбой одного формата уводил весь файл в «не удалось обработать»:
    # остальные форматы не сохранялись, utterances в результат не попадали.
    processor, source = _processor(monkeypatch, tmp_path)
    out = tmp_path / "out"

    def broken_srt(*_args, **_kwargs):
        raise ValueError("cue planner failed")

    monkeypatch.setattr(processor, "_generate_srt", broken_srt)

    result = processor.process_file(str(source), str(out), 0, 1, output_formats=["txt", "srt", "md"])

    assert result["success"] is True
    assert (out / "in.txt").read_text(encoding="utf-8") == "Привет."
    assert (out / "in.md").exists()
    assert not (out / "in.srt").exists()
    assert "cue planner failed" in result["export_errors"]["srt"]
    assert result["utterances"]


def test_all_formats_failing_is_a_failure_with_reason(monkeypatch, tmp_path):
    processor, source = _processor(monkeypatch, tmp_path)

    def broken(*_args, **_kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr("src.core.export.write_text_atomic", broken)

    result = processor.process_file(str(source), str(tmp_path / "out"), 0, 1, output_formats=["txt"])

    assert result["success"] is False
    assert "No space left on device" in result["error"]
    assert result["utterances"]


def test_failed_write_keeps_previous_file_intact(monkeypatch, tmp_path):
    processor, source = _processor(monkeypatch, tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    previous = out / "in.txt"
    previous.write_text("старый результат", encoding="utf-8")

    def failing_replace(_src, _dst):
        raise OSError("disk detached")

    monkeypatch.setattr("src.utils.atomic_json.os.replace", failing_replace)

    processor.process_file(str(source), str(out), 0, 1, output_formats=["txt"])

    assert previous.read_text(encoding="utf-8") == "старый результат"
    assert sorted(path.name for path in out.iterdir()) == ["in.txt"]


def test_explicit_empty_format_list_writes_nothing_even_with_diarization(monkeypatch, tmp_path):
    # MCP/API собирают ответ из result['utterances'] и передают [] — лишний
    # _diarize.txt при успешной диаризации был мусором в рабочем каталоге.
    processor, source = _processor(
        monkeypatch, tmp_path, diarization_manager=_Diarization(), diarization_backend="onnx"
    )
    out = tmp_path / "out"

    result = processor.process_file(
        str(source), str(out), 0, 1,
        output_formats=[], enable_diarization=True, diarization_backend="onnx",
    )

    assert result["success"] is True
    assert result["diarization"]["applied"] is True
    assert result["saved_files"] == []
    assert list(Path(out).iterdir()) == []
    assert result["utterances"][0]["speaker"] == "Спикер №1"


@pytest.mark.parametrize("formats", [None, ["txt"]])
def test_diarized_txt_is_still_added_when_formats_were_requested(monkeypatch, tmp_path, formats):
    processor, source = _processor(
        monkeypatch, tmp_path, diarization_manager=_Diarization(), diarization_backend="onnx"
    )
    out = tmp_path / "out"

    processor.process_file(
        str(source), str(out), 0, 1,
        output_formats=formats, enable_diarization=True, diarization_backend="onnx",
    )

    assert (out / "in_diarize.txt").exists()
