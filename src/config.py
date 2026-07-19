"""
Конфигурация приложения GigaAM v3 Transcriber
Загружает настройки из переменных окружения (.env файл)
"""

import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

APP_CONFIG_DIR_NAME = "GigaAMTranscriber"


def _validate_backend_name(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized not in {"auto", "mlx", "onnx", "pytorch"}:
        raise ValueError(f"Unsupported ASR backend: {normalized}")
    return normalized


def _validate_onnx_provider(value: str | None) -> str:
    normalized = (value or "auto").strip().lower() or "auto"
    if normalized not in {"auto", "cpu", "cuda", "tensorrt", "coreml", "directml"}:
        raise ValueError(f"Unsupported ONNX provider: {normalized}")
    return normalized


def _validate_onnx_quantization(value: str | None) -> str | None:
    normalized = (value or "").strip().lower()
    if normalized in {"", "none"}:
        return None
    if normalized != "int8":
        raise ValueError(f"Unsupported ONNX quantization: {normalized}")
    return normalized


def _parse_bool(value: str | bool | None, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "t", "yes", "y", "on", "enable", "enabled"}:
        return True
    if normalized in {"0", "false", "f", "no", "n", "off", "disable", "disabled"}:
        return False
    return default


def user_config_dir() -> Path:
    """Persistent per-user config directory outside the app bundle."""
    override = os.environ.get("GIGAAM_CONFIG_DIR")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_CONFIG_DIR_NAME
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_CONFIG_DIR_NAME
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_CONFIG_DIR_NAME


def user_env_path() -> Path:
    return user_config_dir() / ".env"


def project_env_path() -> Path:
    return Path(__file__).resolve().parent.parent / ".env"


def save_env_value(key: str, value: str, env_path: Path | None = None) -> Path:
    """Save one KEY=value pair to the persistent user .env file."""
    target = env_path or user_env_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if target.exists():
        lines = [
            line for line in target.read_text(encoding="utf-8").splitlines()
            if not line.startswith(f"{key}=")
        ]
    lines.append(f"{key}={value}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[key] = value
    return target


def _has_cyrillic(text: str) -> bool:
    """Проверяет наличие кириллических символов в строке"""
    return bool(re.search(r'[а-яА-ЯёЁ]', text))


# Папка с моделями, привезёнными офлайн-сборкой (заполняется ниже).
BUNDLED_MODELS_DIR = None


def _setup_huggingface_cache():
    """
    Настраивает директорию кэша HuggingFace.

    Кэш моделей хранится в общей папке приложения ``C:\\GigaAMGUICash\\hf``
    (рядом с рантаймами torch). Путь без кириллицы решает две проблемы Windows:
    символьные ссылки без прав администратора и ошибки на кириллических путях.
    Переопределяется переменной окружения HF_HOME.
    """
    if sys.platform == 'win32':
        # Отключаем использование символьных ссылок в HuggingFace
        # Это предотвращает ошибки с правами доступа
        if 'HF_HUB_DISABLE_SYMLINKS_WARNING' not in os.environ:
            os.environ['HF_HUB_DISABLE_SYMLINKS_WARNING'] = '1'

    # Если HF_HOME не задан явно — используем общий кэш приложения.
    global BUNDLED_MODELS_DIR
    if 'HF_HOME' not in os.environ:
        try:
            from .utils.runtime_manager import bundled_hf_cache_dir, hf_cache_dir
            # Офлайн-сборка везёт модели папкой рядом с собой: без этой ветки
            # приложение полезло бы за ними в сеть, хотя они уже лежат рядом.
            BUNDLED_MODELS_DIR = bundled_hf_cache_dir()
            hf_dir = BUNDLED_MODELS_DIR or hf_cache_dir()
        except Exception:
            hf_dir = Path("C:/GigaAMGUICash/hf")
        try:
            hf_dir.mkdir(parents=True, exist_ok=True)
            os.environ['HF_HOME'] = str(hf_dir)
        except OSError as e:
            print(f"Не удалось создать директорию кэша HuggingFace: {e}")


# Загрузка переменных из .env файла
def load_env():
    """Загружает переменные окружения из .env файла"""
    # User config wins for packaged .app and survives app replacement.
    for env_path in (user_env_path(), project_env_path(), Path(__file__).resolve().parent.parent.parent / ".env"):
        if env_path.exists():
            load_dotenv(env_path, override=False)


# Настраиваем HuggingFace cache ДО загрузки переменных окружения
_setup_huggingface_cache()

# Загружаем переменные окружения
load_env()

# HuggingFace токен для доступа к моделям
HF_TOKEN = os.getenv("HF_TOKEN", "")

if HF_TOKEN and HF_TOKEN.startswith("hf_"):
    os.environ["HF_TOKEN"] = HF_TOKEN

# Настройки модели
MODEL_NAME = os.getenv("MODEL_NAME", "ai-sage/GigaAM-v3")
MODEL_REVISION = os.getenv("MODEL_REVISION", "e2e_rnnt")

# ASR backend strategy. Офлайн-сборка везёт только ONNX-цепочку, а auto выбрал
# бы MLX или PyTorch и полез бы за ними в сеть — ровно то, ради чего затевался
# офлайн-вариант. Явная настройка пользователя по-прежнему главнее.
_DEFAULT_ASR_BACKEND = "onnx" if BUNDLED_MODELS_DIR else "auto"
ASR_BACKEND = _validate_backend_name(os.getenv("ASR_BACKEND", _DEFAULT_ASR_BACKEND))
# По той же причине диаризация по умолчанию тоже ONNX: pyannote требует torch
# и токен HuggingFace, которых в офлайн-наборе нет.
DIARIZATION_BACKEND = (
    os.getenv("DIARIZATION_BACKEND", "onnx" if BUNDLED_MODELS_DIR else "pyannote")
    .strip()
    .lower()
    or "pyannote"
)
ASR_MODEL = os.getenv("ASR_MODEL", MODEL_REVISION)
ASR_ALLOW_FALLBACK = _parse_bool(os.getenv("ASR_ALLOW_FALLBACK"), default=True)
ASR_SEGMENTATION_MODE = os.getenv("ASR_SEGMENTATION_MODE", "vad").strip().lower()
if ASR_SEGMENTATION_MODE not in {"vad", "overlap_chunks", "fixed_chunks"}:
    ASR_SEGMENTATION_MODE = "vad"
# Keep the additional segmentation model off the ASR accelerator by default:
# pyannote VAD next to GigaAM on the same GPU/MPS is a real OOM source.
ASR_VAD_DEVICE = os.getenv("ASR_VAD_DEVICE", "cpu").strip().lower() or "cpu"
MLX_MODEL_REPO = os.getenv("MLX_MODEL_REPO", "aystream/GigaAM-v3-e2e-rnnt-mlx")
ONNX_PROVIDER = _validate_onnx_provider(os.getenv("ONNX_PROVIDER"))
ONNX_QUANTIZATION = _validate_onnx_quantization(os.getenv("ONNX_QUANTIZATION"))
ONNX_MODEL_DIR = os.getenv("ONNX_MODEL_DIR", "").strip() or None
ONNX_VAD_MODEL = os.getenv("ONNX_VAD_MODEL", "silero").strip() or "silero"


# Настройки аудио конвертации
AUDIO_SAMPLE_RATE = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))
AUDIO_CHANNELS = int(os.getenv("AUDIO_CHANNELS", "1"))
AUDIO_PREPROCESSING_MODE = os.getenv("AUDIO_PREPROCESSING_MODE", "auto").strip().lower()
if AUDIO_PREPROCESSING_MODE not in {"off", "auto", "light", "denoise"}:
    AUDIO_PREPROCESSING_MODE = "auto"

# Настройки GUI
APP_TITLE = os.getenv("APP_TITLE", "GigaAM v3 Transcriber")
APP_GEOMETRY = os.getenv("APP_GEOMETRY", "900x700")
APP_THEME = os.getenv("APP_THEME", "blue")

# Файл статистики
STATS_FILE = os.getenv("STATS_FILE", "processing_stats.json")

# Настройки LLM API (OpenAI-compatible или Anthropic Messages API)
LLM_API_URL = os.getenv("LLM_API_URL", "https://api.openai.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))

# Поддерживаемые форматы файлов (входные)
SUPPORTED_FORMATS = (
    'Media files',
    '*.mp3 *.wav *.m4a *.aac *.mp4 *.avi *.mov *.mkv *.webm *.flac *.ogg *.wma *.qta *.3gp'
)

# Список расширений для фильтрации (один источник правды для GUI и drag-and-drop)
MEDIA_EXTENSIONS = (
    '.mp3', '.wav', '.m4a', '.aac', '.mp4', '.avi', '.mov', '.mkv', '.webm',
    '.flac', '.ogg', '.wma', '.qta', '.3gp'
)

# Поддерживаемые форматы выходных файлов.
# Ключи txt_* управляют отдельными текстовыми файлами; md/srt/vtt — самостоятельные форматы.
OUTPUT_FORMATS = {
    'txt':                  'Текст (.txt)',
    'txt_timecodes':        'Таймкоды (_timecodes.txt)',
    'txt_diarize':          'Диаризация (_diarize.txt)',
    'txt_diarize_timecodes': 'Диар.+тайм. (_diarize_timecodes.txt)',
    'md':                   'Markdown (.md)',
    'srt':                  'SRT (.srt)',
    'vtt':                  'VTT (.vtt)',
}
