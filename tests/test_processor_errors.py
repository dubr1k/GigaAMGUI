"""Сбой обработки должен доходить до клиента причиной, а не «см. журнал сервера».

web («Обработка не удалась»), MCP и OpenAI-совместимый API видят только
словарь результата: без поля ``error`` пользователь не узнаёт, что случилось.
"""

from pathlib import Path

import pytest

from src.core.processor import TranscriptionProcessor
from src.core.progress import ProgressEvent


class _Stats:
    pass


class _Loader:
    requested_provider = "auto"

    def __init__(self, result=None, error: Exception | None = None):
        self._result = result if result is not None else [
            {"transcription": "привет", "boundaries": (0.0, 1.0)}
        ]
        self._error = error

    def transcribe_longform(self, audio_path, progress_callback=None):
        if self._error is not None:
            raise self._error
        return self._result


def _input(tmp_path: Path) -> Path:
    path = tmp_path / "in.wav"
    path.write_bytes(b"stub")
    return path


def _processor(monkeypatch, tmp_path, loader, **kwargs):
    logs: list[str] = []
    processor = TranscriptionProcessor(loader, _Stats(), logger=logs.append, **kwargs)
    path = _input(tmp_path)
    monkeypatch.setattr(processor.audio_converter, "convert_to_wav", lambda *a, **k: str(path))
    monkeypatch.setattr("src.core.processor.AudioConverter.get_media_duration", lambda _p: 1.0)
    return processor, path, logs


def _run(processor, path, **kwargs):
    return processor.process_file(str(path), str(path.parent), 0, 1, **kwargs)


def test_success_result_carries_empty_error(monkeypatch, tmp_path):
    processor, path, _logs = _processor(monkeypatch, tmp_path, _Loader())

    result = _run(processor, path)

    assert result["success"] is True
    assert result["error"] is None


def test_conversion_failure_reports_converter_reason(monkeypatch, tmp_path):
    processor = TranscriptionProcessor(_Loader(), _Stats(), logger=lambda _m: None)
    monkeypatch.setattr("src.core.processor.AudioConverter.get_media_duration", lambda _p: 0.0)
    missing = tmp_path / "missing.mp3"

    result = processor.process_file(str(missing), str(tmp_path), 0, 1)

    assert result["success"] is False
    assert "файл не найден" in result["error"]
    assert str(missing) in result["error"]


def test_conversion_failure_without_converter_reason_still_has_error(monkeypatch, tmp_path):
    processor, path, _logs = _processor(monkeypatch, tmp_path, _Loader())
    monkeypatch.setattr(processor.audio_converter, "convert_to_wav", lambda *a, **k: None)

    result = _run(processor, path)

    assert result["success"] is False
    assert result["error"]


def test_asr_exception_is_reported_in_result(monkeypatch, tmp_path):
    processor, path, _logs = _processor(
        monkeypatch, tmp_path, _Loader(error=RuntimeError("CUDA out of memory"))
    )

    result = _run(processor, path)

    assert result["success"] is False
    assert "CUDA out of memory" in result["error"]


def test_asr_value_error_is_not_blamed_on_vad_token(monkeypatch, tmp_path):
    # Любой ValueError ASR раньше объявлялся ошибкой детектора речи с
    # подсказкой про HF_TOKEN, без traceback. Bad input ≠ проблема токена.
    processor, path, logs = _processor(
        monkeypatch, tmp_path, _Loader(error=ValueError("sample_rate должен быть положительным"))
    )

    result = _run(processor, path)

    assert result["success"] is False
    assert "sample_rate должен быть положительным" in result["error"]
    assert not any("детектора речи" in line for line in logs)
    assert not any("HF_TOKEN" in line for line in logs)


def test_export_failure_is_reported_in_result(monkeypatch, tmp_path):
    processor, path, _logs = _processor(monkeypatch, tmp_path, _Loader())

    def broken_markdown(*_args, **_kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr(processor, "_generate_markdown", broken_markdown)

    result = _run(processor, path, output_formats=["md"])

    assert result["error"]
    assert "No space left on device" in result["error"]


def test_diarization_factory_failure_surfaces_real_cause(monkeypatch, tmp_path):
    # torch DLL не грузится (WinError 1114): фабрика падала, процессор писал
    # в результат «Проверьте HF_TOKEN» и советовал принять лицензии pyannote.
    monkeypatch.setenv("HF_TOKEN", "hf_valid")

    def broken_factory(*_args, **_kwargs):
        raise OSError("[WinError 1114] A dynamic link library (DLL) initialization routine failed")

    monkeypatch.setattr("src.core.diarization.factory.create_diarization_backend", broken_factory)
    processor, path, logs = _processor(monkeypatch, tmp_path, _Loader(), diarization_backend="pyannote")

    result = _run(processor, path, enable_diarization=True, diarization_backend="pyannote")

    assert result["success"] is True
    error = result["diarization"]["error"]
    assert "WinError 1114" in error
    assert "HF_TOKEN" not in error
    assert not any("условия ВСЕХ моделей" in line for line in logs)


def test_apply_diarization_chains_factory_exception(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_valid")
    cause = OSError("[WinError 1114] DLL")
    monkeypatch.setattr(
        "src.core.diarization.factory.create_diarization_backend",
        lambda *_a, **_k: (_ for _ in ()).throw(cause),
    )
    processor = TranscriptionProcessor(_Loader(), _Stats(), logger=lambda _m: None, diarization_backend="pyannote")

    with pytest.raises(RuntimeError) as excinfo:
        processor._apply_diarization("a.wav", [{"transcription": "x", "boundaries": (0.0, 1.0)}])

    assert excinfo.value.__cause__ is cause


def _event() -> ProgressEvent:
    return ProgressEvent(stage="transcription", stage_progress=0.5, file_progress=0.5)


def test_progress_callback_internal_type_error_is_not_retried_as_legacy():
    calls = []

    def callback(event):
        calls.append(event)
        raise TypeError("ошибка внутри клиента")

    processor = TranscriptionProcessor(_Loader(), _Stats(), progress_callback=callback)

    with pytest.raises(TypeError, match="ошибка внутри клиента"):
        processor._emit_progress(_event())
    assert len(calls) == 1


def test_legacy_two_argument_progress_callback_is_called_once():
    calls = []
    processor = TranscriptionProcessor(
        _Loader(), _Stats(), progress_callback=lambda stage, value: calls.append((stage, value))
    )

    processor._emit_progress(_event())

    assert calls == [("transcription", 0.5)]


def test_event_progress_callback_with_optional_legacy_argument_gets_event():
    calls = []

    def callback(event_or_stage, value=None):
        calls.append((event_or_stage, value))

    processor = TranscriptionProcessor(_Loader(), _Stats(), progress_callback=callback)
    event = _event()

    processor._emit_progress(event)

    assert calls == [(event, None)]
