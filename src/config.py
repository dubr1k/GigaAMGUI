"""
Конфигурация приложения GigaAM v3 Transcriber
Загружает настройки из переменных окружения (.env файл)
"""

import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv


def _has_cyrillic(text: str) -> bool:
    """Проверяет наличие кириллических символов в строке"""
    return bool(re.search(r'[а-яА-ЯёЁ]', text))


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
    if 'HF_HOME' not in os.environ:
        try:
            from .utils.runtime_manager import hf_cache_dir
            hf_dir = hf_cache_dir()
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
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        load_dotenv(env_path, override=False)
    else:
        # Пытаемся загрузить из корня проекта
        root_env = Path(__file__).parent.parent.parent / '.env'
        if root_env.exists():
            load_dotenv(root_env, override=False)


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

# Настройки аудио конвертации
AUDIO_SAMPLE_RATE = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))
AUDIO_CHANNELS = int(os.getenv("AUDIO_CHANNELS", "1"))

# Настройки GUI
APP_TITLE = os.getenv("APP_TITLE", "GigaAM v3 Transcriber")
APP_GEOMETRY = os.getenv("APP_GEOMETRY", "900x700")
APP_THEME = os.getenv("APP_THEME", "blue")

# Файл статистики
STATS_FILE = os.getenv("STATS_FILE", "processing_stats.json")

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
