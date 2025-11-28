# API Документация GigaAM v3 Transcriber

Документация программного интерфейса для разработчиков, желающих интегрировать или расширить функциональность приложения.

## Содержание

- [Архитектура проекта](#архитектура-проекта)
- [Основные модули](#основные-модули)
- [API классов](#api-классов)
- [Примеры использования](#примеры-использования)
- [Расширение функциональности](#расширение-функциональности)

---

## Архитектура проекта

Проект построен по модульной архитектуре с разделением на слои:

```
src/
├── config.py           # Конфигурация
├── core/              # Бизнес-логика
│   ├── model_loader.py
│   └── processor.py
├── gui/               # Графический интерфейс
│   └── app.py
└── utils/             # Вспомогательные утилиты
    ├── audio_converter.py
    ├── time_formatter.py
    ├── processing_stats.py
    └── pyannote_patch.py
```

---

## Основные модули

### src/config.py

Централизованная конфигурация приложения.

**Константы:**
```python
HF_TOKEN: str              # Токен HuggingFace
MODEL_NAME: str            # Имя модели GigaAM
MODEL_REVISION: str        # Ревизия модели
AUDIO_SAMPLE_RATE: int     # Частота дискретизации (16000 Hz)
AUDIO_CHANNELS: int        # Количество каналов (1 - моно)
APP_TITLE: str             # Название приложения
APP_GEOMETRY: str          # Размеры окна
STATS_FILE: str            # Файл статистики
SUPPORTED_FORMATS: tuple   # Поддерживаемые форматы файлов
```

**Использование:**
```python
from src.config import HF_TOKEN, MODEL_NAME, AUDIO_SAMPLE_RATE
```

---

## API классов

### ModelLoader

Класс для загрузки и управления моделью GigaAM.

**Расположение:** `src/core/model_loader.py`

#### Конструктор

```python
ModelLoader()
```

Создает экземпляр загрузчика модели. При инициализации модель еще не загружена.

#### Методы

##### load_model()

```python
def load_model() -> tuple
```

Загружает модель GigaAM и pipeline для сегментации.

**Возвращает:**
- `tuple`: (model, sample_rate, pipeline, device)
  - `model`: Модель GigaAM
  - `sample_rate`: Частота дискретизации (16000)
  - `pipeline`: Pipeline pyannote для сегментации
  - `device`: Устройство (cpu/cuda/mps)

**Исключения:**
- `Exception`: При ошибке загрузки модели или токена

**Пример:**
```python
from src.core.model_loader import ModelLoader

loader = ModelLoader()
model, sample_rate, pipeline, device = loader.load_model()
```

---

### TranscriptionProcessor

Класс для обработки транскрибации аудио/видео файлов.

**Расположение:** `src/core/processor.py`

#### Конструктор

```python
TranscriptionProcessor(
    model,
    sample_rate: int,
    pipeline,
    device,
    progress_callback: callable = None
)
```

**Параметры:**
- `model`: Загруженная модель GigaAM
- `sample_rate`: Частота дискретизации
- `pipeline`: Pipeline для сегментации
- `device`: Устройство для вычислений
- `progress_callback`: Функция обратного вызова для прогресса (опционально)

**Пример:**
```python
from src.core.processor import TranscriptionProcessor

processor = TranscriptionProcessor(
    model, 
    sample_rate, 
    pipeline, 
    device,
    progress_callback=lambda p: print(f"Progress: {p}%")
)
```

#### Методы

##### process_file()

```python
def process_file(
    file_path: str,
    output_dir: str = None
) -> tuple
```

Обрабатывает один аудио/видео файл.

**Параметры:**
- `file_path`: Путь к файлу
- `output_dir`: Директория для сохранения результатов (опционально)

**Возвращает:**
- `tuple`: (text_output, timecodes_output)
  - `text_output`: Путь к файлу с чистым текстом
  - `timecodes_output`: Путь к файлу с таймкодами

**Исключения:**
- `FileNotFoundError`: Файл не найден
- `Exception`: Ошибка при обработке

**Пример:**
```python
text_file, timecodes_file = processor.process_file(
    "audio.mp3",
    output_dir="/path/to/output"
)
```

##### process_multiple_files()

```python
def process_multiple_files(
    file_paths: list,
    output_dir: str = None
) -> list
```

Обрабатывает несколько файлов последовательно.

**Параметры:**
- `file_paths`: Список путей к файлам
- `output_dir`: Директория для сохранения (опционально)

**Возвращает:**
- `list`: Список кортежей (text_output, timecodes_output) для каждого файла

**Пример:**
```python
files = ["audio1.mp3", "audio2.wav"]
results = processor.process_multiple_files(files)
```

---

### AudioConverter

Класс для конвертации аудио/видео через FFmpeg.

**Расположение:** `src/utils/audio_converter.py`

#### Методы

##### convert_to_wav()

```python
@staticmethod
def convert_to_wav(
    input_path: str,
    output_path: str = None,
    sample_rate: int = 16000,
    channels: int = 1
) -> str
```

Конвертирует аудио/видео в WAV формат.

**Параметры:**
- `input_path`: Путь к исходному файлу
- `output_path`: Путь для сохранения WAV (опционально)
- `sample_rate`: Частота дискретизации (по умолчанию 16000)
- `channels`: Количество каналов (по умолчанию 1)

**Возвращает:**
- `str`: Путь к созданному WAV файлу

**Исключения:**
- `FileNotFoundError`: FFmpeg не найден
- `Exception`: Ошибка конвертации

**Пример:**
```python
from src.utils.audio_converter import AudioConverter

wav_path = AudioConverter.convert_to_wav(
    "video.mp4",
    sample_rate=16000,
    channels=1
)
```

##### check_ffmpeg()

```python
@staticmethod
def check_ffmpeg() -> bool
```

Проверяет доступность FFmpeg.

**Возвращает:**
- `bool`: True если FFmpeg доступен

**Пример:**
```python
if AudioConverter.check_ffmpeg():
    print("FFmpeg установлен")
```

---

### TimeFormatter

Утилиты для форматирования времени.

**Расположение:** `src/utils/time_formatter.py`

#### Методы

##### seconds_to_timestamp()

```python
@staticmethod
def seconds_to_timestamp(seconds: float) -> str
```

Конвертирует секунды в формат HH:MM:SS.

**Параметры:**
- `seconds`: Количество секунд

**Возвращает:**
- `str`: Форматированная строка времени

**Пример:**
```python
from src.utils.time_formatter import TimeFormatter

timestamp = TimeFormatter.seconds_to_timestamp(125.5)
# Результат: "00:02:05"
```

##### format_duration()

```python
@staticmethod
def format_duration(seconds: float) -> str
```

Форматирует длительность в читаемый вид.

**Параметры:**
- `seconds`: Количество секунд

**Возвращает:**
- `str`: Читаемая строка (например, "2 мин 5 сек")

**Пример:**
```python
duration = TimeFormatter.format_duration(125)
# Результат: "2 мин 5 сек"
```

---

### ProcessingStats

Класс для сбора и анализа статистики обработки.

**Расположение:** `src/utils/processing_stats.py`

#### Конструктор

```python
ProcessingStats(stats_file: str = "processing_stats.json")
```

**Параметры:**
- `stats_file`: Путь к файлу статистики

#### Методы

##### add_record()

```python
def add_record(
    file_size: int,
    duration: float,
    processing_time: float,
    file_type: str
) -> None
```

Добавляет запись о обработке файла.

**Параметры:**
- `file_size`: Размер файла в байтах
- `duration`: Длительность аудио в секундах
- `processing_time`: Время обработки в секундах
- `file_type`: Тип файла (расширение)

**Пример:**
```python
from src.utils.processing_stats import ProcessingStats

stats = ProcessingStats()
stats.add_record(
    file_size=5000000,
    duration=120,
    processing_time=45,
    file_type="mp3"
)
```

##### estimate_time()

```python
def estimate_time(
    file_size: int,
    duration: float,
    file_type: str
) -> float
```

Оценивает время обработки на основе статистики.

**Параметры:**
- `file_size`: Размер файла
- `duration`: Длительность аудио
- `file_type`: Тип файла

**Возвращает:**
- `float`: Оценочное время в секундах

**Пример:**
```python
estimated = stats.estimate_time(
    file_size=10000000,
    duration=300,
    file_type="wav"
)
print(f"Примерное время: {estimated} секунд")
```

---

### GigaTranscriberApp

Главный класс GUI приложения.

**Расположение:** `src/gui/app.py`

Класс наследуется от `customtkinter.CTk` и реализует графический интерфейс.

#### Конструктор

```python
GigaTranscriberApp()
```

Создает и инициализирует GUI приложение.

#### Основные методы

##### select_files()

```python
def select_files() -> None
```

Открывает диалог выбора файлов.

##### select_output_dir()

```python
def select_output_dir() -> None
```

Открывает диалог выбора директории для сохранения.

##### start_processing()

```python
def start_processing() -> None
```

Запускает обработку выбранных файлов в отдельном потоке.

---

## Примеры использования

### Простая транскрибация файла

```python
from src.core.model_loader import ModelLoader
from src.core.processor import TranscriptionProcessor

# Загрузка модели
loader = ModelLoader()
model, sample_rate, pipeline, device = loader.load_model()

# Создание процессора
processor = TranscriptionProcessor(model, sample_rate, pipeline, device)

# Обработка файла
text_file, timecodes_file = processor.process_file("audio.mp3")

print(f"Результат: {text_file}")
```

### Транскрибация с прогресс-баром

```python
from src.core.model_loader import ModelLoader
from src.core.processor import TranscriptionProcessor

def progress_callback(progress):
    print(f"Прогресс: {progress}%", end='\r')

loader = ModelLoader()
model, sample_rate, pipeline, device = loader.load_model()

processor = TranscriptionProcessor(
    model, 
    sample_rate, 
    pipeline, 
    device,
    progress_callback=progress_callback
)

text_file, timecodes_file = processor.process_file("long_audio.mp3")
```

### Пакетная обработка файлов

```python
from src.core.model_loader import ModelLoader
from src.core.processor import TranscriptionProcessor
import glob

loader = ModelLoader()
model, sample_rate, pipeline, device = loader.load_model()
processor = TranscriptionProcessor(model, sample_rate, pipeline, device)

# Найти все MP3 файлы в папке
files = glob.glob("audio/*.mp3")

# Обработать все файлы
results = processor.process_multiple_files(files, output_dir="results/")

for text_file, timecodes_file in results:
    print(f"Обработан: {text_file}")
```

### Конвертация видео в аудио

```python
from src.utils.audio_converter import AudioConverter

# Проверка FFmpeg
if not AudioConverter.check_ffmpeg():
    raise Exception("FFmpeg не установлен")

# Конвертация
wav_file = AudioConverter.convert_to_wav(
    "video.mp4",
    sample_rate=16000,
    channels=1
)

print(f"Создан WAV: {wav_file}")
```

### Использование статистики

```python
from src.utils.processing_stats import ProcessingStats
import time

stats = ProcessingStats()

# Перед обработкой
file_size = 10000000  # 10 MB
duration = 300  # 5 минут
estimated_time = stats.estimate_time(file_size, duration, "mp3")
print(f"Ожидаемое время: {estimated_time:.1f} сек")

# Обработка файла
start = time.time()
# ... обработка ...
processing_time = time.time() - start

# Сохранение статистики
stats.add_record(file_size, duration, processing_time, "mp3")
```

---

## Расширение функциональности

### Добавление нового формата вывода

Создайте новый класс экспорта в `src/utils/`:

```python
# src/utils/exporter.py

class TranscriptionExporter:
    @staticmethod
    def export_to_srt(segments: list, output_path: str) -> None:
        """Экспорт в формат SRT субтитров"""
        with open(output_path, 'w', encoding='utf-8') as f:
            for i, segment in enumerate(segments, 1):
                start = TimeFormatter.seconds_to_timestamp(segment['start'])
                end = TimeFormatter.seconds_to_timestamp(segment['end'])
                text = segment['text']
                
                f.write(f"{i}\n")
                f.write(f"{start} --> {end}\n")
                f.write(f"{text}\n\n")
    
    @staticmethod
    def export_to_json(segments: list, output_path: str) -> None:
        """Экспорт в JSON формат"""
        import json
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(segments, f, ensure_ascii=False, indent=2)
```

### Добавление предобработки аудио

```python
# src/utils/audio_processor.py

import soundfile as sf
import numpy as np

class AudioPreprocessor:
    @staticmethod
    def remove_silence(audio_path: str, output_path: str) -> str:
        """Удаление тишины из аудио"""
        audio, sr = sf.read(audio_path)
        
        # Вычисление энергии
        energy = np.abs(audio)
        threshold = np.mean(energy) * 0.1
        
        # Удаление тихих участков
        mask = energy > threshold
        audio_trimmed = audio[mask]
        
        # Сохранение
        sf.write(output_path, audio_trimmed, sr)
        return output_path
    
    @staticmethod
    def normalize_audio(audio_path: str, output_path: str) -> str:
        """Нормализация громкости"""
        audio, sr = sf.read(audio_path)
        
        # Нормализация
        audio_normalized = audio / np.max(np.abs(audio))
        
        sf.write(output_path, audio_normalized, sr)
        return output_path
```

### Создание плагина для постобработки

```python
# src/plugins/postprocessor.py

class TextPostprocessor:
    @staticmethod
    def fix_punctuation(text: str) -> str:
        """Исправление пунктуации"""
        # Ваша логика
        return text
    
    @staticmethod
    def remove_filler_words(text: str) -> str:
        """Удаление слов-паразитов"""
        fillers = ['ээ', 'ммм', 'э-э', 'м-м']
        for filler in fillers:
            text = text.replace(filler, '')
        return text
    
    @staticmethod
    def apply_all(text: str) -> str:
        """Применить всю постобработку"""
        text = TextPostprocessor.fix_punctuation(text)
        text = TextPostprocessor.remove_filler_words(text)
        return text
```

### Интеграция с внешним API

```python
# src/integrations/api_client.py

import requests

class TranscriptionAPI:
    def __init__(self, api_url: str, api_key: str):
        self.api_url = api_url
        self.api_key = api_key
    
    def upload_transcription(self, text: str, metadata: dict) -> dict:
        """Отправка транскрибации на внешний сервер"""
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }
        
        payload = {
            'text': text,
            'metadata': metadata
        }
        
        response = requests.post(
            f"{self.api_url}/transcriptions",
            json=payload,
            headers=headers
        )
        
        return response.json()
```

---

## События и обратные вызовы

### Подписка на события обработки

```python
class ProcessingEvents:
    def on_start(self, file_path: str):
        """Вызывается при начале обработки"""
        print(f"Начата обработка: {file_path}")
    
    def on_progress(self, progress: int):
        """Вызывается при обновлении прогресса"""
        print(f"Прогресс: {progress}%")
    
    def on_complete(self, text_file: str, timecodes_file: str):
        """Вызывается при завершении"""
        print(f"Завершено: {text_file}")
    
    def on_error(self, error: Exception):
        """Вызывается при ошибке"""
        print(f"Ошибка: {error}")

# Использование
events = ProcessingEvents()
processor = TranscriptionProcessor(
    model, sample_rate, pipeline, device,
    progress_callback=events.on_progress
)
```

---

## Тестирование

### Юнит-тесты

```python
# tests/test_processor.py

import unittest
from src.core.processor import TranscriptionProcessor

class TestTranscriptionProcessor(unittest.TestCase):
    def setUp(self):
        # Настройка перед каждым тестом
        pass
    
    def test_process_file(self):
        # Тест обработки файла
        pass
    
    def tearDown(self):
        # Очистка после теста
        pass

if __name__ == '__main__':
    unittest.main()
```

---

## Дополнительные ресурсы

- [HuggingFace Transformers Документация](https://huggingface.co/docs/transformers)
- [PyTorch Документация](https://pytorch.org/docs/stable/index.html)
- [Pyannote Audio Документация](https://github.com/pyannote/pyannote-audio)
- [CustomTkinter Документация](https://customtkinter.tomschimansky.com/)

---

Для вопросов и предложений создавайте issue на GitHub.

