"""TUI не должен предлагать бэкенд, которого нет в зависимостях его воркера."""

import re
from pathlib import Path

TUI_SOURCE = Path("tui/src/main.rs")
TUI_REQUIREMENTS = Path("requirements-tui.txt")


def _requirement_names() -> set[str]:
    names = set()
    for line in TUI_REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("--"):
            continue
        names.add(re.split(r"[<>=!\[]", line, maxsplit=1)[0].strip().lower())
    return names


def test_tui_offers_onnx_and_the_worker_can_run_it():
    """Команды /backend onnx и /onnx-provider есть — значит нужен onnx-asr."""
    source = TUI_SOURCE.read_text(encoding="utf-8")
    assert '"onnx"' in source
    assert "/onnx-provider" in source

    names = _requirement_names()
    assert "onnx-asr" in names
    assert "onnxruntime" in names


def test_worker_requirements_cover_onnx_diarization():
    """ONNX-диаризация ресемплит через soxr — без него падает на не-16 кГц."""
    assert "soxr" in _requirement_names()


def test_pytorch_line_stays_bounded():
    """torchaudio 2.9+ снёс legacy backend API, который импортирует pyannote 3.1.1."""
    text = TUI_REQUIREMENTS.read_text(encoding="utf-8")

    for package in ("torch", "torchaudio"):
        assert f"{package}>=2.6.0,<2.9.0" in text
