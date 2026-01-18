"""
Конфигурация приложения GigaAM v3 Transcriber
Загружает настройки из переменных окружения (.env файл)
"""

import os
import sys
import re
from pathlib import Path
from dotenv import load_dotenv


def _has_cyrillic(text: str) -> bool:
    """Проверяет наличие кириллических символов в строке"""
    return bool(re.search(r'[а-яА-ЯёЁ]', text))


def _setup_huggingface_cache():
    """
    Настраивает директорию кэша HuggingFace для избежания проблем на Windows.
    
    Проблемы на Windows:
    1. Символьные ссылки требуют прав администратора
    2. Пути с кириллицей могут вызывать ошибки
    
    Решение: устанавливаем HF_HOME в путь без кириллицы
    """
    # Определяем текущий путь к домашней директории
    home_path = os.path.expanduser("~")
    
    # Проверяем наличие кириллицы в пути
    if _has_cyrillic(home_path):
        print("=" * 60)
        print("⚠️  ПРЕДУПРЕЖДЕНИЕ: Путь к профилю пользователя содержит кириллицу!")
        print(f"    Путь: {home_path}")
        print()
        print("    Это может вызвать проблемы с кэшированием моделей HuggingFace.")
        print()
        print("    Рекомендации:")
        print("    1. Создайте папку для кэша без кириллицы в пути")
        print("       Например: C:\\HuggingFaceCache")
        print()
        print("    2. Установите переменную окружения HF_HOME:")
        print("       set HF_HOME=C:\\HuggingFaceCache")
        print()
        print("    Или добавьте в файл .env:")
        print("       HF_HOME=C:\\HuggingFaceCache")
        print("=" * 60)
    
    # На Windows устанавливаем специальные настройки
    if sys.platform == 'win32':
        # Отключаем использование символьных ссылок в HuggingFace
        # Это предотвращает ошибки с правами доступа
        if 'HF_HUB_DISABLE_SYMLINKS_WARNING' not in os.environ:
            os.environ['HF_HUB_DISABLE_SYMLINKS_WARNING'] = '1'
        
        # Если HF_HOME не установлен и путь содержит кириллицу,
        # предлагаем альтернативный путь
        if 'HF_HOME' not in os.environ and _has_cyrillic(home_path):
            # Пробуем использовать C:\HuggingFaceCache как альтернативу
            alt_cache = Path("C:/HuggingFaceCache")
            if not alt_cache.exists():
                try:
                    alt_cache.mkdir(parents=True, exist_ok=True)
                    os.environ['HF_HOME'] = str(alt_cache)
                    print(f"    Создана альтернативная директория кэша: {alt_cache}")
                except Exception as e:
                    print(f"    Не удалось создать альтернативную директорию: {e}")
            else:
                os.environ['HF_HOME'] = str(alt_cache)
                print(f"    Используется альтернативная директория кэша: {alt_cache}")


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

# Проверка и установка токена
if HF_TOKEN and HF_TOKEN.startswith("hf_"):
    os.environ["HF_TOKEN"] = HF_TOKEN
    print("Токен HuggingFace загружен из .env файла")
else:
    print("ВНИМАНИЕ: Токен HuggingFace не установлен или неверный!")
    print("Пожалуйста, установите ваш токен в файле .env")
    print("Скопируйте .env.example в .env и добавьте свой токен")
    print("Токен должен начинаться с 'hf_'")
    print("\nИнструкция:")
    print("1. Зарегистрируйтесь на https://huggingface.co")
    print("2. Создайте токен: https://huggingface.co/settings/tokens")
    print("3. Примите условия: https://huggingface.co/pyannote/segmentation-3.0")
    print("4. Скопируйте .env.example в .env: cp .env.example .env")
    print("5. Замените 'your_huggingface_token_here' на ваш токен в .env")

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

# Поддерживаемые форматы файлов
SUPPORTED_FORMATS = (
    'Media files',
    '*.mp3 *.wav *.m4a *.mp4 *.avi *.mov *.mkv *.webm *.flac *.ogg *.wma'
)