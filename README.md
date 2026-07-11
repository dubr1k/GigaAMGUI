# GigaAM v3 Transcriber

**🇷🇺 Русский** · [🇺🇸 English version](README_EN.md)

Русскоязычная транскрибация аудио и видео с **Desktop GUI**, **CLI**, **REST API** и защищённым **Web GUI** на базе **GigaAM-v3**.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![PyQt6](https://img.shields.io/badge/Desktop-PyQt6-41CD52)
![FastAPI](https://img.shields.io/badge/API-FastAPI-009688)
![Docker](https://img.shields.io/badge/Web-Docker-2496ED)
![Stars](https://img.shields.io/github/stars/dubr1k/GigaAMGUI?style=social)

</div>

---

## Скриншоты

### Обработка

![Обработка](https://i.postimg.cc/1XVNkf3V/Processing.png)

### Вкладка LLM

![LLM](https://i.postimg.cc/7bvTwwCy/LLM-tab.png)

### Настройки LLM

![Настройки LLM](https://i.postimg.cc/c1PDr3Yk/LLM-settings.png)

---

## Что это

**GigaAM v3 Transcriber** — это полноценный инструмент вокруг GigaAM-v3, а не просто обёртка над моделью.

## Возможности

- Desktop GUI, CLI, REST API и Web GUI
- Пакетная обработка файлов и папок
- Рекурсивное сканирование папок и drag & drop
- Скачивание медиа через `yt-dlp`
- Диаризация спикеров через `pyannote`
- MLX RNN-T для ускоренного распознавания на macOS Apple Silicon
- Экспорт в `txt`, `txt_timecodes`, `txt_diarize`, `txt_diarize_timecodes`, `md`, `srt`, `vtt`
- Встроенная LLM-вкладка для:
  - выжимок
  - задач / action items
  - кастомных промптов
- LLM-провайдеры:
  - OpenAI-compatible API
  - Claude Code
  - Codex
  - OpenCode
  - Pi
  - произвольный внешний CLI
- Переключение интерфейса RU/EN
- Переключение темы
- Single-instance поведение приложения
- Журнал обработки, прогресс, отмена
- Web GUI с авторизацией, восстановлением истории, SSE-прогрессом и Docker hardening
- Поддержка CPU, CUDA, Intel XPU, Apple Silicon MPS

## ASR backend

На macOS Apple Silicon режим `auto` использует [gigaam-mlx](https://github.com/aystream/gigaam-mlx) и MLX RNN-T. Если MLX недоступен, приложение может переключиться на PyTorch fallback. Windows, Linux и Intel Mac продолжают использовать PyTorch.

Выбрать движок можно в Desktop GUI (`Настройки → Движок распознавания…`) или в CLI:

```bash
python cli.py --backend auto -f audio.wav
python cli.py --backend mlx -f audio.wav
python cli.py --backend pytorch -f audio.wav
```

MLX применяется только для распознавания речи. Диаризация через `pyannote` во всех случаях использует PyTorch.

## Быстрый старт

```bash
git clone https://github.com/dubr1k/GigaAMGUI.git
cd GigaAMGUI
cp .env.example .env
pip install -r requirements.txt
```

Минимальный `.env`:

```env
HF_TOKEN=your_huggingface_token_here
```

Для Web GUI:

```env
WEB_SECRET=change_me
WEB_USERNAME=admin
WEB_PASSWORD=strong_password
```

Проверьте FFmpeg:

```bash
ffmpeg -version
```

## Запуск

Desktop GUI:

```bash
python app.py
```

CLI:

```bash
python cli.py
python cli.py -d /path/to/files -o /path/to/output
python cli.py -f audio.mp3 -f video.mp4 -o /path/to/output
```

REST API:

```bash
python api.py
```

Доступно:

- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/redoc`
- `http://127.0.0.1:8000/health`

Web GUI:

```bash
docker compose up -d --build gigaam-web
```

По умолчанию:

- `http://127.0.0.1:8001/`
- `http://127.0.0.1:8001/health`

## Требования

- Python 3.10+
- FFmpeg в `PATH`
- HuggingFace token
- Для диаризации нужно принять условия:
  - `pyannote/speaker-diarization-3.1`
  - `pyannote/segmentation-3.0`

## RTX 50xx / Blackwell

```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements.txt
```

## Структура проекта

```text
GigaAMGUI/
├── app.py
├── cli.py
├── api.py
├── web/
├── src/
├── tests/
├── packaging/          # PyInstaller specs + build scripts
├── pyinstaller_hooks/
├── docs/               # documentation
├── assets/             # icons, screenshots
├── Dockerfile
└── docker-compose.yml
```


## Благодарности

- [SaluteDevices / GigaAM](https://github.com/salute-developers/GigaAM)
- [GigaAM-v3 на HuggingFace](https://huggingface.co/ai-sage/GigaAM-v3)
- [aystream / gigaam-mlx](https://github.com/aystream/gigaam-mlx) — MLX-порт GigaAM RNN-T и основа интеграции для Apple Silicon
