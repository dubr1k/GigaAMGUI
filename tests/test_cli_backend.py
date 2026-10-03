"""CLI test coverage for ASR backend selection."""

from typing import Any

import pytest
from click.testing import CliRunner

import cli


def _run_cli_with_fake_loader(tmp_path, monkeypatch, args, results=None):
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"audio")

    capture: dict[str, Any] = {}

    class FakeLoader:
        def __init__(self, requested_backend=None, *_, **__):
            capture["requested_backend"] = requested_backend or ""
            capture.update(__)

        def load_model(self, logger=None):
            return True

        def is_loaded(self):
            return True

        def transcribe_longform(self, _):
            return []

    def _fake_process_files_with_progress(*_, **kwargs):
        capture["process_kwargs"] = kwargs
        if isinstance(results, BaseException):
            raise results
        return list(results or [])

    monkeypatch.setattr(cli, "ModelLoader", FakeLoader)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(cli, "process_files_with_progress", _fake_process_files_with_progress)
    # Минимизируем сайд-эффекты процесса инициализации (веб/GUI утилиты из app_context)
    monkeypatch.setattr(cli, "setup_logger", lambda: None)

    runner = CliRunner()
    invocation = ["--files", str(sample), *args, "--no-interactive"]
    if not any(flag in args for flag in ("--diarize", "--no-diarize")):
        invocation.append("--no-diarize")
    result = runner.invoke(cli.main, invocation)
    return result, capture, sample


def test_cli_accepts_backend_option(tmp_path, monkeypatch):
    result, capture, _ = _run_cli_with_fake_loader(
        tmp_path,
        monkeypatch,
        ["--backend", "mlx"],
    )

    assert result.exit_code == 0
    assert capture["requested_backend"] == "mlx"


def test_cli_accepts_onnx_backend_and_provider(tmp_path, monkeypatch):
    result, capture, _ = _run_cli_with_fake_loader(
        tmp_path,
        monkeypatch,
        ["--backend", "onnx", "--onnx-provider", "cuda"],
    )

    assert result.exit_code == 0
    assert capture["requested_backend"] == "onnx"
    assert capture["onnx_provider"] == "cuda"


def test_cli_accepts_asr_model_option(tmp_path, monkeypatch):
    result, capture, _ = _run_cli_with_fake_loader(
        tmp_path,
        monkeypatch,
        ["--backend", "onnx", "--model", "multilingual_large_ctc"],
    )

    assert result.exit_code == 0
    assert capture["model_revision"] == "multilingual_large_ctc"


def test_cli_uses_default_backend_from_config_when_not_passed(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ASR_BACKEND", "pytorch")
    result, capture, _ = _run_cli_with_fake_loader(
        tmp_path,
        monkeypatch,
        [],
    )

    assert result.exit_code == 0
    assert capture["requested_backend"] == "pytorch"


def test_cli_sortformer_does_not_require_hf_token(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    result, capture, _ = _run_cli_with_fake_loader(
        tmp_path,
        monkeypatch,
        ["--diarize", "--diarization-backend", "sortformer"],
    )

    assert result.exit_code == 0
    assert capture["process_kwargs"]["diarization_backend"] == "sortformer"


def test_cli_onnx_diarization_does_not_require_hf_token(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    result, capture, _ = _run_cli_with_fake_loader(
        tmp_path,
        monkeypatch,
        ["--diarize", "--diarization-backend", "onnx"],
    )

    assert result.exit_code == 0
    assert capture["process_kwargs"]["diarization_backend"] == "onnx"


def test_cli_forwards_audio_preprocessing_mode(tmp_path, monkeypatch):
    result, capture, _ = _run_cli_with_fake_loader(
        tmp_path,
        monkeypatch,
        ["--audio-preprocessing", "off"],
    )

    assert result.exit_code == 0
    assert capture["process_kwargs"]["audio_preprocessing_mode"] == "off"


def test_cli_forwards_subtitle_options(tmp_path, monkeypatch):
    result, capture, _ = _run_cli_with_fake_loader(
        tmp_path,
        monkeypatch,
        [
            "--no-subtitle-sentence-split",
            "--subtitle-max-lines", "3",
            "--subtitle-max-width", "72",
        ],
    )

    assert result.exit_code == 0
    options = capture["process_kwargs"]["subtitle_options"]
    assert not options.sentence_split
    assert options.max_line_count == 3
    assert options.max_line_width == 72


def _result(sample, success: bool) -> dict:
    return {"success": success, "file_path": str(sample), "total_time": 0.5, "media_duration": 2.0}


def test_cli_exits_zero_when_every_file_succeeds(tmp_path, monkeypatch):
    sample = tmp_path / "sample.mp3"
    result, _capture, _ = _run_cli_with_fake_loader(tmp_path, monkeypatch, [], results=[_result(sample, True)])
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("outcomes", [[False], [True, False]], ids=["all-failed", "one-of-two-failed"])
def test_cli_exits_nonzero_when_a_file_fails(tmp_path, monkeypatch, outcomes):
    # Скрипт/CI должен видеть провал по коду выхода, а не разбирать таблицу
    sample = tmp_path / "sample.mp3"
    result, _capture, _ = _run_cli_with_fake_loader(
        tmp_path, monkeypatch, [], results=[_result(sample, ok) for ok in outcomes])
    assert result.exit_code == 1, result.output


def test_cli_ctrl_c_exits_130(tmp_path, monkeypatch):
    result, _capture, _ = _run_cli_with_fake_loader(tmp_path, monkeypatch, [], results=KeyboardInterrupt())
    assert result.exit_code == 130, result.output
    assert "прервана" in result.output


def test_cli_sortformer_rejects_fixed_speaker_count(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    result, _capture, _ = _run_cli_with_fake_loader(
        tmp_path,
        monkeypatch,
        [
            "--diarize",
            "--diarization-backend",
            "sortformer",
            "--speakers",
            "2",
        ],
    )

    assert result.exit_code != 0
    assert "определяет число спикеров автоматически" in result.output


def test_cli_backend_and_provider_choices_follow_runtime_options():
    from src.core.runtime_options import ASR_BACKENDS, ONNX_PROVIDERS

    choices = {param.name: list(param.type.choices) for param in cli.main.params if param.name in ("backend", "onnx_provider")}
    assert choices == {"backend": list(ASR_BACKENDS), "onnx_provider": list(ONNX_PROVIDERS)}


def test_cli_names_the_reason_when_the_model_cannot_load(tmp_path, monkeypatch):
    # Без -v причина (например, нет весов MLX в офлайн-кэше) терялась за
    # общим «Не удалось загрузить модель!».
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"audio")

    class FailingLoader:
        def __init__(self, *_, **__):
            pass

        def load_model(self, logger=None):
            logger("Движок распознавания: mlx")
            logger("Не удалось загрузить модель MLX (rnnt, repo): LocalEntryNotFoundError: no cached snapshot")
            logger("Не удалось загрузить модель через движок mlx")
            return False

    monkeypatch.setattr(cli, "ModelLoader", FailingLoader)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(cli, "setup_logger", lambda: None)

    result = CliRunner().invoke(cli.main, ["--files", str(sample), "--no-interactive", "--no-diarize"])

    assert result.exit_code == 1
    # rich переносит длинные строки по ширине терминала
    assert "LocalEntryNotFoundError: no cached snapshot" in " ".join(result.output.split())
