"""CLI test coverage for ASR backend selection."""

from typing import Any

from click.testing import CliRunner

import cli


def _run_cli_with_fake_loader(tmp_path, monkeypatch, args):
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"audio")

    capture: dict[str, Any] = {}

    class FakeLoader:
        def __init__(self, requested_backend=None, *_, **__):
            capture["requested_backend"] = requested_backend or ""

        def load_model(self, logger=None):
            return True

        def is_loaded(self):
            return True

        def transcribe_longform(self, _):
            return []

    def _fake_process_files_with_progress(*_, **kwargs):
        capture["process_kwargs"] = kwargs
        return []

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


def test_cli_forwards_audio_preprocessing_mode(tmp_path, monkeypatch):
    result, capture, _ = _run_cli_with_fake_loader(
        tmp_path,
        monkeypatch,
        ["--audio-preprocessing", "off"],
    )

    assert result.exit_code == 0
    assert capture["process_kwargs"]["audio_preprocessing_mode"] == "off"


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
