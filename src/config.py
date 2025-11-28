"""
Конфигурация приложения GigaAM v3 Transcriber
Загружает настройки из переменных окружения (.env файл)
"""

import os
from pathlib import Path

# Загрузка переменных из .env файла
def load_env():
    """Загружает переменные окружения из .env файла"""
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()

# Загружаем переменные окружения
load_env()

# HuggingFace токен для доступа к моделям
HF_TOKEN = os.getenv("HF_TOKEN", "")

# Проверка и установка токена
if HF_TOKEN and HF_TOKEN.startswith("hf_"):
    os.environ["HF_TOKEN"] = HF_TOKEN
    print(f"Токен HuggingFace установлен: {HF_TOKEN[:10]}...")
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