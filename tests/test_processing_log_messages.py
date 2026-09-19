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


# Реальный журнал одного файла (tests/fixtures/liquid_smoke.wav через tui_worker)
# плюс строки путей с ошибками; всё это должно уходить в английский интерфейс.
SAMPLE_JOURNAL = """Загружаем модель распознавания речи…
Движок распознавания: auto
Загружаем модель для Apple Silicon (MLX): aystream/GigaAM-v3-e2e-rnnt-mlx
При первом запуске модель скачивается — это может занять несколько минут.
Подготавливаем модель распознавания речи (GigaAM-v3)…
Вычисления выполняются на устройстве: MPS
Модель готова.
Модель готова (ONNX, v3_e2e_rnnt); вычисления выполняются на: cpu
Модель загружена.
Файл 1 из 1: liquid_smoke.wav
Длительность записи: 0:03
Подготавливаем звук: liquid_smoke.wav → WAV 16 кГц…
Файл: /Users/x/liquid_smoke.wav
Звук подготовлен.
Очистка звука: лёгкая очистка от шума (режим auto)
  Почему: речь слабо выделяется на фоне шума
  Почему: в записи постоянный фоновый шум
  Очистка не применена: средство очистки завершилось с ошибкой, использована исходная запись
Распознаём речь…
Речь найдена: участков — 1, фрагментов для распознавания — 1
Распознавание завершено: фрагментов текста — 1
Определяем, кто говорит (pyannote)…
Определение говорящих выполняется на: устройство mps
Говорящих найдено: 2
Реплик в тексте: 1
Объём текста: 56 символов
Сохранён файл: liquid_smoke.txt
Готово за 21 сек (подготовка звука 0.3 с, распознавание 21.0 с)
FFmpeg не смог подготовить звук (код ошибки 254). Подробности ниже:
  FFmpeg: Error opening output file
Файл пропущен: liquid_smoke.wav
Не удалось обработать a.mp3: boom
Не удалось определить говорящих, текст сохранён без разметки по говорящим: 401
Внимание: VAD недоступен (RuntimeError): проверьте локальный кэш или HF_TOKEN и доступ к pyannote/segmentation-3.0; использовано резервное разбиение по тихим точкам с перекрытием
Внимание: VAD отключён настройкой ASR_SEGMENTATION_MODE: использовано разбиение по тихим точкам с перекрытием
Внимание: CUDAExecutionProvider завершил inference с ошибкой RuntimeError: cuda; транскрибация начата заново на CPUExecutionProvider, прогресс отсчитывается с нуля
Внимание: определение говорящих переключилось на запасной режим — Аварийный fallback: MLX не загрузился, использован PyTorch backend
Внимание: говорящих определить не удалось, файл _diarize.txt не создан
Определение говорящих выполняется на: устройство cpu, провайдер CPUExecutionProvider
Готово за 2 мин 3 сек (подготовка звука 1.2 с, распознавание 118.4 с)
Остановка запрошена: закончим текущий файл и остановимся.
Не удалось загрузить модель распознавания"""


def test_english_translation_covers_a_real_journal() -> None:
    from src.core.log_i18n import translate_log_line

    untranslated = [
        line for line in SAMPLE_JOURNAL.split("\n")
        if translate_log_line(line) == line and re.search(r"[А-Яа-яЁё]", line)
    ]
    assert not untranslated, untranslated
    # В образце все подстановки латинские, поэтому кириллицы в переводе быть не должно:
    # она означает непереведённую вложенную фразу (причину, единицу времени, устройство).
    for line in SAMPLE_JOURNAL.split("\n"):
        translated = translate_log_line(line)
        assert not re.search(r"[А-Яа-яЁё]", translated), translated


def test_english_translation_recurses_into_reasons() -> None:
    from src.core.log_i18n import translate_log, translate_log_line

    assert translate_log_line("Файл 2 из 5: interview.mp3") == "File 2 of 5: interview.mp3"
    assert translate_log_line("  Почему: в записи постоянный фоновый шум") == "  Why: constant background noise in the recording"
    assert translate_log_line("Очистка звука: лёгкая очистка от шума (режим auto)") == "Audio cleanup: light noise cleanup (mode auto)"
    assert translate_log_line("Сохранён файл: Почему.txt") == "Saved file: Почему.txt"
    assert translate_log_line("Что-то новое") == "Что-то новое"
    assert translate_log("Модель готова.\n/tmp/x.wav") == "Model ready.\n/tmp/x.wav"


def test_swift_log_translation_is_generated_from_the_python_table() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "scripts/gen_log_translation_swift.py", "--check"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_every_preprocessing_action_has_a_russian_rendering() -> None:
    source = Path(audio_preprocessing.__file__).read_text(encoding="utf-8")
    actions = set(re.findall(r"action=\"([a-z_]+)\"", source))
    assert actions <= set(processor_module._PREPROCESSING_ACTIONS_RU), actions - set(processor_module._PREPROCESSING_ACTIONS_RU)
