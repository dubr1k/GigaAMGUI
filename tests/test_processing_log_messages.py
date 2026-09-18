"""Журнал обработки читают люди без технического бэкграунда.

Сообщения формируются один раз в src.core / src.utils и попадают во все
клиенты (PyQt, web, CLI, TUI, GigaAMLiquid), поэтому понятный русский язык
обеспечивается у источника, а не фильтрами на стороне клиента.
"""

import re
from pathlib import Path

import pytest

from src.core import processor as processor_module
from src.utils import audio_preprocessing

LOG_SOURCES = [
    Path("src/core/processor.py"),
    Path("src/core/model_loader.py"),
    Path("src/core/asr/pytorch_backend.py"),
    Path("src/core/asr/mlx_backend.py"),
    Path("src/core/asr/onnx_backend.py"),
    Path("src/utils/audio_converter.py"),
    Path("src/tui_worker.py"),
]

# Обрывки жаргона, которые раньше уходили пользователю дословно.
JARGON = [
    "DEBUG:",
    "ОШИБКА VAD",
    "КРИТИЧЕСКАЯ ОШИБКА",
    "ASR сегментация",
    "окон декодера",
    "Пример структуры сегмента",
    "backend load requested",
    "load failed: backend=",
    "Запрошен ASR backend",
    "provider chain=",
    "watchdog'ом",
    "ffmpeg код",
    "Loading GigaAM model",
    "GigaAM model ready",
    "Failed to load model",
    "Cancellation requested",
    "Error while processing",
]


def _logged_strings(source: str) -> list[str]:
    """Строковые литералы внутри вызовов logger/_log (с f-строками включительно)."""
    calls = re.findall(r"(?:self\.)?(?:_log|logger|_logger)\(\s*((?:f?\"[^\"]*\"\s*\+?\s*)+)", source)
    return [literal for call in calls for literal in re.findall(r"\"([^\"]*)\"", call)]


@pytest.mark.parametrize("path", LOG_SOURCES, ids=str)
def test_log_messages_avoid_developer_jargon(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    offenders = [text for text in _logged_strings(source) if any(word in text for word in JARGON)]
    assert not offenders, offenders


def test_worker_logs_in_russian() -> None:
    source = Path("src/tui_worker.py").read_text(encoding="utf-8")
    logged = re.findall(r"self\._log\(\s*f?\"([^\"]+)\"", source)
    assert logged, "worker должен писать в журнал"
    for text in logged:
        assert re.search(r"[А-Яа-яЁё]", text), text


def test_every_preprocessing_reason_has_a_russian_rendering() -> None:
    """Новая причина в audio_preprocessing.py без перевода не должна пройти незамеченной."""
    source = Path(audio_preprocessing.__file__).read_text(encoding="utf-8")
    reasons = set(re.findall(r"reasons(?:=\(|\.append\()\s*\"([^\"]+)\"", source))
    reasons |= set(re.findall(r"return False, \"([^\"]+)\"", source))
    assert len(reasons) >= 15
    for reason in reasons:
        rendered = processor_module._preprocessing_reason_ru(reason)
        assert rendered != reason, f"нет перевода: {reason}"
        assert re.search(r"[А-Яа-яЁё]", rendered), rendered
    assert processor_module._preprocessing_reason_ru("Something new") == "Something new"
    assert processor_module._preprocessing_reason_ru("Quality gate could not validate candidate: boom") == (
        "не удалось проверить результат очистки: boom"
    )


def test_every_preprocessing_action_has_a_russian_rendering() -> None:
    source = Path(audio_preprocessing.__file__).read_text(encoding="utf-8")
    actions = set(re.findall(r"action=\"([a-z_]+)\"", source))
    assert actions <= set(processor_module._PREPROCESSING_ACTIONS_RU), actions - set(processor_module._PREPROCESSING_ACTIONS_RU)
