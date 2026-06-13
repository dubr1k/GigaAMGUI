# GigaAM v3 Transcriber

Программа для расшифровки русской речи из аудио и видео. В основе используется модель **GigaAM-v3** от **SaluteDevices**.

Проект можно использовать как обычное desktop-приложение, CLI-утилиту или REST API сервис.

## Что умеет программа

- Распознаёт русскую речь в аудио и видеофайлах.
- Работает с отдельными файлами и папками.
- В GUI рекурсивно сканирует папки и подпапки.
- Принимает файлы через выбор в диалоге и drag & drop.
- Скачивает медиа по ссылке через `yt-dlp` и добавляет его в очередь транскрибации.
- Поддерживает входные форматы: `mp3`, `wav`, `m4a`, `aac`, `flac`, `ogg`, `wma`, `mp4`, `avi`, `mov`, `mkv`, `webm`, `qta`, `3gp`.
- Делит длинные записи на части для обработки.
- Показывает общий прогресс и прогресс текущего файла.
- Оценивает примерное время обработки на основе статистики прошлых запусков.
- Может определять разных говорящих через диаризацию спикеров.
- Сохраняет результат в нескольких форматах:
  - обычный текст `.txt`;
  - текст с таймкодами `.txt`;
  - Markdown `.md`;
  - субтитры `.srt`;
  - субтитры `.vtt`.
- Может работать с GPU-ускорением: CUDA на Windows/Linux, XPU на Intel Arc, MPS на macOS.
- Ведёт логи запусков и автоматически очищает старые логи.
- Хранит секреты и настройки в `.env`, без хардкода токенов в коде.
- Даёт REST API для загрузки файлов, отслеживания задач и скачивания результатов.

## Варианты запуска

### GUI

```bash
python app.py
```

Для macOS/Linux также есть скрипт:

```bash
./scripts/run_gui.sh
```

Для Windows:

```cmd
scripts\run_gui.bat
```

GUI подходит для ручной работы: выбрать файлы, папку, ссылку, форматы вывода и запустить обработку.

### CLI

Интерактивный режим:

```bash
python cli.py
```

Обработка папки:

```bash
python cli.py -d /path/to/files -o /path/to/output
```

Обработка конкретных файлов:

```bash
python cli.py -f audio.mp3 -f video.mp4 -o /path/to/output
```

Неинтерактивный режим для скриптов:

```bash
python cli.py -d /data/incoming -o /data/results -n -v
```

### REST API

Запуск сервера:

```bash
python api.py
```

После запуска доступны:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- Healthcheck: `http://localhost:8000/health`

Пример загрузки файла:

```bash
curl -X POST "http://localhost:8000/api/v1/transcribe" \
  -H "X-API-Key: YOUR_API_KEY" \
  -F "file=@audio.mp3"
```

## Как выглядит результат

Обычный текст:

```text
Добрый день, коллеги. Начинаем встречу.
Сегодня обсудим план работ и сроки.
```

Текст с таймкодами:

```text
[00:00:00 - 00:00:17] Добрый день, коллеги. Начинаем встречу.
[00:00:18 - 00:00:39] Сегодня обсудим план работ и сроки.
```

Текст с диаризацией:

```text
[Спикер №1]
Добрый день, коллеги. Начинаем встречу.

[Спикер №2]
Готов представить текущий статус проекта.
```

SRT:

```srt
1
00:00:00,000 --> 00:00:17,500
Добрый день, коллеги. Начинаем встречу.

2
00:00:18,000 --> 00:00:39,200
Сегодня обсудим план работ и сроки.
```

## Установка

### Общие требования

- Python 3.10 или выше.
- FFmpeg в `PATH`.
- Git для установки GigaAM из репозитория.
- 8 GB RAM минимум, 16 GB желательно.
- 10 GB свободного места на диске.
- HuggingFace token для загрузки моделей и диаризации.

### Базовая установка

```bash
git clone https://github.com/dubr1k/GigaAMGUI.git
cd GigaAMGUI
pip install -r requirements.txt
cp .env.example .env
```

Откройте `.env` и укажите HuggingFace token:

```env
HF_TOKEN=hf_your_token_here
```

Токен нужен для доступа к моделям HuggingFace. Для диаризации также нужно принять условия моделей `pyannote` на HuggingFace.

### FFmpeg

Проверьте, что FFmpeg установлен:

```bash
ffmpeg -version
```

Если команда не найдена, установите FFmpeg:

- Windows: через официальный билд с `ffmpeg.org` и добавление в `PATH`;
- macOS: `brew install ffmpeg`;
- Linux: `sudo apt install ffmpeg`.

## Настройка

Основные параметры лежат в `.env`.

Пример:

```env
HF_TOKEN=hf_your_token_here

API_HOST=127.0.0.1
API_PORT=8000
MAX_FILE_SIZE=2147483648
MAX_CONCURRENT_TASKS=3
UPLOAD_DIR=uploads
RESULTS_DIR=results
```

Файл `.env` не должен попадать в Git.

## Диаризация

Диаризация определяет, кто говорит в разные моменты записи.

В GUI её можно включить чекбоксом **"Включить диаризацию спикеров"**. Количество спикеров можно указать вручную или оставить пустым для автоопределения.

Перед использованием примите условия моделей:

- `pyannote/speaker-diarization-3.1`
- `pyannote/segmentation-3.0`

## Где сохраняются файлы

В GUI и CLI результат по умолчанию сохраняется рядом с исходным файлом. Можно выбрать отдельную папку вывода.

В API результат сохраняется в директорию, указанную в `RESULTS_DIR`.

## Структура проекта

```text
GigaAMGUI/
├── app.py                 # запуск GUI
├── cli.py                 # CLI
├── api.py                 # REST API
├── requirements.txt       # зависимости
├── .env.example           # пример конфигурации
├── src/
│   ├── config.py          # настройки и переменные окружения
│   ├── core/              # загрузка модели и обработка файлов
│   ├── gui/               # PyQt6 интерфейс
│   └── utils/             # конвертация, диаризация, логи, загрузка медиа
├── scripts/               # скрипты запуска
├── deploy/                # файлы для production-развёртывания API
└── docs/                  # дополнительная документация
```

## Частые проблемы

### Не найден FFmpeg

Установите FFmpeg и проверьте, что команда доступна:

```bash
ffmpeg -version
```

### HuggingFace token не настроен

Проверьте, что `.env` существует и содержит:

```env
HF_TOKEN=hf_your_token_here
```

### Нет доступа к pyannote

Нужно войти на HuggingFace и принять условия использования моделей:

- `pyannote/speaker-diarization-3.1`
- `pyannote/segmentation-3.0`

### Проблемы на Windows с кириллицей в пути

Если путь пользователя содержит кириллицу, задайте отдельный кэш HuggingFace:

```cmd
set HF_HOME=C:\HuggingFaceCache
```

Или добавьте в `.env`:

```env
HF_HOME=C:\HuggingFaceCache
```

### RTX 5090 / Blackwell

Для RTX 5090 требуется PyTorch с CUDA 12.8:

```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements.txt
```

## Благодарности

Проект использует модель **GigaAM-v3** от **SaluteDevices**.

Модель доступна на HuggingFace:

```text
https://huggingface.co/ai-sage/GigaAM-v3
```

Официальный репозиторий GigaAM:

```text
https://github.com/salute-developers/GigaAM
```
