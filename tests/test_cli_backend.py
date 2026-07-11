"""CLI test coverage for ASR backend selection."""


from click.testing import CliRunner

import cli


def _run_cli_with_fake_loader(tmp_path, monkeypatch, args):
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"audio")

    capture: dict[str, str] = {}

    class FakeLoader:
        def __init__(self, requested_backend=None, *_, **__):
            capture["requested_backend"] = requested_backend or ""

        def load_model(self, logger=None):
            return True

        def is_loaded(self):
            return True

        def transcribe_longform(self, _):
            return []

    def _fake_process_files_with_progress(*_, **__):
        return []

    monkeypatch.setattr(cli, "ModelLoader", FakeLoader)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(cli, "process_files_with_progress", _fake_process_files_with_progress)
    # Минимизируем сайд-эффекты процесса инициализации (веб/GUI утилиты из app_context)
    monkeypatch.setattr(cli, "setup_logger", lambda: None)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["--files", str(sample), *args, "--no-interactive", "--no-diarize"])
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
