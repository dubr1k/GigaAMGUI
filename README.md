# GigaAM v3 Transcriber

Программа для расшифровки русской речи из аудио и видео. В основе используется модель **GigaAM-v3** от **SaluteDevices**.

Проект можно использовать как обычное desktop-приложение, CLI-утилиту, REST API сервис или защищённый Web GUI в Docker.

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
- Даёт Web GUI для загрузки файлов и ссылок через браузер, просмотра задач и скачивания результатов.

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

### Web GUI в Docker

Актуальный Web GUI запускается через Docker Compose и слушает внутренний порт контейнера `8000`. В production порт опубликован только на localhost:

```yaml
ports:
  - "127.0.0.1:8001:8000"
```

Публичный доступ должен идти через reverse proxy, например nginx, который проксирует HTTPS-трафик в `http://127.0.0.1:8001`.

Локальный запуск:

```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
# вставьте результат в WEB_SECRET
docker compose up -d --build gigaam-web
```

После запуска доступны:

- Web GUI: `http://127.0.0.1:8001/`
- Healthcheck: `http://127.0.0.1:8001/health`

Для Web GUI обязательны переменные окружения:

```env
HF_TOKEN=hf_your_token_here
WEB_SECRET=replace_with_32plus_byte_random_secret
WEB_USERNAME=dubr1k
WEB_PASSWORD=replace_with_strong_password
```

Контейнер запускается с усиленной изоляцией: пользователь `gigaam`, `read_only: true`, `no-new-privileges:true`, ограничение памяти и процессов. Для работы HuggingFace/pyannote при read-only rootfs отдельно смонтирован writable-кэш `/home/gigaam/.cache`.

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

WEB_SECRET=replace_with_32plus_byte_random_secret
WEB_USERNAME=dubr1k
WEB_PASSWORD=replace_with_strong_password
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

В Web GUI контейнер использует bind mounts:

- `uploads/` -> временные загруженные файлы;
- `results/` -> результаты и статистика обработки;
- `logs/` -> прикладные логи;
- `cache/home-gigaam-cache/` -> кэш HuggingFace/pyannote для longform-сегментации.

Эти директории являются runtime-данными и не коммитятся.

## Структура проекта

```text
GigaAMGUI/
├── app.py                 # запуск GUI
├── cli.py                 # CLI
├── api.py                 # REST API
├── web/                   # Web GUI на FastAPI + статический фронтенд
├── Dockerfile             # образ Web GUI
├── docker-compose.yml     # запуск Web GUI с GPU и hardening
├── .dockerignore          # исключения для Docker build context
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

## Production-развёртывание Web GUI

На production-хосте источник правды для запущенного контейнера должен совпадать с этим репозиторием. Проверить фактический путь можно так:

```bash
docker inspect gigaam-web --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}'
```

Ожидаемая схема:

1. `docker compose up -d --build gigaam-web` собирает и запускает контейнер.
2. Docker публикует только `127.0.0.1:8001`.
3. Nginx принимает HTTPS на публичном домене и проксирует в `http://127.0.0.1:8001`.
4. `.env` хранит реальные `HF_TOKEN`, `WEB_SECRET`, `WEB_USERNAME`, `WEB_PASSWORD` и не попадает в Git.
5. Runtime-директории `uploads/`, `results/`, `logs/`, `cache/` не коммитятся.

Быстрая проверка после деплоя:

```bash
curl -fsS http://127.0.0.1:8001/health
docker ps --filter name=gigaam-web
docker logs --tail 100 gigaam-web
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

### Web GUI не принимает пароль

Проверьте, что `.env` содержит актуальные `WEB_USERNAME` и `WEB_PASSWORD`, затем пересоздайте контейнер:

```bash
docker compose up -d gigaam-web
```

Если менялся `WEB_SECRET`, старые cookie станут недействительными: выйдите из Web GUI или очистите cookie сайта.

### Longform-транскрибация падает на read-only filesystem

При `read_only: true` HuggingFace/pyannote всё равно пишет кэш в домашнюю директорию пользователя. В `docker-compose.yml` должен быть mount:

```yaml
volumes:
  - ./cache/home-gigaam-cache:/home/gigaam/.cache
```

Без него возможна ошибка вида:

```text
OSError: [Errno 30] Read-only file system: '/home/gigaam/.cache'
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
