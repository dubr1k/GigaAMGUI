# GigaAM v3 Transcriber

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![Desktop: PyQt6](https://img.shields.io/badge/Desktop-PyQt6-41CD52)](https://www.riverbankcomputing.com/software/pyqt/)
[![API: FastAPI](https://img.shields.io/badge/API-FastAPI-009688)](https://fastapi.tiangolo.com/)
[![Web: Docker](https://img.shields.io/badge/Web-Docker-2496ED)](https://www.docker.com/)
[![GitHub stars](https://img.shields.io/github/stars/dubr1k/GigaAMGUI?style=social)](https://github.com/dubr1k/GigaAMGUI/stargazers)

**🇷🇺 Русский** · [🇺🇸 English](README_EN.md)

Транскрибация русской речи из аудио и видео на базе **GigaAM-v3**. Один сервисный слой, пять интерфейсов: Desktop GUI, CLI, REST API, Web GUI и terminal TUI.

> GigaAM Transcriber — полноценный workflow для расшифровки, экспорта, диаризации и LLM-постобработки, а не только обёртка над моделью.

## Содержание

- [Возможности](#возможности)
- [Быстрый старт](#быстрый-старт)
- [Интерфейсы](#интерфейсы)
- [Конфигурация](#конфигурация)
- [Интеллектуальная подготовка аудио](#интеллектуальная-подготовка-аудио)
- [ASR backend](#asr-backend)
- [Структура](#структура)
- [Скриншоты](#скриншоты)
- [Благодарности](#благодарности)

## Возможности

- Пакетная обработка файлов и папок, рекурсивный поиск, drag & drop, загрузка через `yt-dlp`.
- Экспорт: `txt`, `txt_timecodes`, `txt_diarize`, `txt_diarize_timecodes`, `md`, `srt`, `vtt`.
- Выбираемая диаризация: `pyannote` или NVIDIA Streaming Sortformer v2.1.
- Автоматическая диагностика качества, консервативная очистка и safe fallback без сдвига таймкодов.
- Ускорение MLX RNN-T на Apple Silicon; CPU, CUDA, Intel XPU и MPS.
- LLM-постобработка: выжимки, задачи и свои промпты.
- Провайдеры LLM: OpenAI-compatible API, Claude Code, Codex, OpenCode, Pi и произвольный CLI.
- RU/EN, светлая/тёмная тема, журнал, stage-aware progress и отмена очереди.
- Web UI с авторизацией, SSE-прогрессом, восстановлением задач и Docker hardening.

## Быстрый старт

### 1. Установите зависимости

```bash
git clone https://github.com/dubr1k/GigaAMGUI.git
cd GigaAMGUI
cp .env.example .env
python -m pip install -r requirements.txt
ffmpeg -version
```

### 2. Укажите Hugging Face token

```env
HF_TOKEN=your_huggingface_token_here
```

Для диаризации нужно принять условия моделей `pyannote/speaker-diarization-3.1` и `pyannote/segmentation-3.0`.

### Опционально: NVIDIA Sortformer

В полной macOS `.app` Sortformer и NeMo уже включены. При запуске проекта из
исходников Sortformer устанавливается отдельно, чтобы не добавлять тяжёлый NeMo
в базовую установку:

```bash
python -m pip install -r requirements-sortformer.txt
python cli.py --diarize --diarization-backend sortformer -f audio.wav
```

Используется `nvidia/diar_streaming_sortformer_4spk-v2.1` с официальными
high-latency параметрами model card. Модель сама определяет активных
спикеров, поддерживает максимум четыре голоса и не требует `HF_TOKEN`.
Диаризация не зависит от ASR-модели: она проверена с `v3_e2e_rnnt`,
`multilingual_ctc` (220M) и `multilingual_large_ctc` (600M). Обе CTC-модели
работают через PyTorch backend.
Рекомендуется CUDA; CPU работает значительно медленнее. MPS не поддерживается
NeMo и переключается на CPU. Модель (~471 МБ) загружается при первом запуске.
NeMo из Space (`2.5.3`) намеренно не используется из-за исправленных в новых
релизах уязвимостей; optional-файл фиксирует проверенную безопасную ветку 2.7.
Для Web GUI соберите расширенный образ: `INSTALL_SORTFORMER=1 docker compose build gigaam-web`.

## Интерфейсы

| Интерфейс | Запуск | Для чего |
|---|---|---|
| Desktop GUI | `python app.py` | Обычная интерактивная работа |
| CLI | `python cli.py -f audio.wav -o output` | Скрипты и автоматизация |
| REST API | `python api.py` | Интеграции; docs: `http://127.0.0.1:8000/docs` |
| Web GUI | `docker compose up -d --build gigaam-web` | Локальная web-панель: `http://127.0.0.1:8001/` |
| TUI *(preview)* | `cd tui && cargo run --release` | Терминальная интерактивная очередь |

### TUI

```bash
curl -fsSL https://raw.githubusercontent.com/dubr1k/GigaAMGUI/main/scripts/install_tui.sh | bash
gigaam
```

## Конфигурация

Для Web UI задайте в `.env`:

```env
WEB_SECRET=change_me
WEB_USERNAME=admin
WEB_PASSWORD=replace_with_strong_password
```

Для RTX 50xx / Blackwell сначала установите совместимый PyTorch:

```bash
python -m pip install torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
python -m pip install -r requirements.txt
```

## Интеллектуальная подготовка аудио

По умолчанию `AUDIO_PREPROCESSING_MODE=auto`. Перед ASR приложение измеряет
громкость, noise floor, приблизительный SNR, clipping, тишину, DC offset,
spectral flatness и низкочастотный шум. Детерминированная policy выбирает одно
из действий:

- pass-through для уже качественной записи;
- только нормализацию для тихой записи;
- мягкий FFmpeg high-pass/denoise для умеренного шума;
- DeepFilterNet для сильного широкополосного шума;
- отказ от enhancement при клиппинге, почти пустой записи или неуверенном результате.

После обработки кандидат измеряется повторно. Он используется только если
quality gate подтверждает улучшение без роста клиппинга, потери речи и изменения
длительности. ASR получает выбранную дорожку, а диаризация — исходный canonical
WAV: это сохраняет тембр спикеров, границы реплик и таймкоды. Паузы физически не
удаляются.

DeepFilterNet запускается официальным self-contained Rust binary версии `0.5.6`.
Он скачивается с GitHub Releases только при первом обнаружении тяжёлого шума,
проверяется по закреплённому SHA-256 и хранится в runtime cache. Python-пакет
DeepFilterNet не устанавливается и не конфликтует с NumPy 2. Поддерживаются
Windows x64, macOS Intel/Apple Silicon и Linux x64/arm64. При недоступной сети,
неподдерживаемой платформе или любой ошибке транскрибация продолжится с исходной
дорожкой.

```env
AUDIO_PREPROCESSING_MODE=auto  # off | auto | light | denoise
# GIGAAM_DEEPFILTER_DIR=/writable/executable/cache
```

```bash
python cli.py --audio-preprocessing auto -f noisy.wav
python cli.py --audio-preprocessing off -f studio.wav
```

## ASR backend

`auto` на macOS Apple Silicon использует [gigaam-mlx](https://github.com/aystream/gigaam-mlx), затем при необходимости переключается на PyTorch. Остальные платформы используют PyTorch.

```bash
python cli.py --backend auto -f audio.wav
python cli.py --backend mlx -f audio.wav
python cli.py --backend pytorch -f audio.wav
```

MLX применяется только к ASR; диаризация всегда использует PyTorch.

## Структура

```text
GigaAMGUI/
├── app.py                 # PyQt desktop app
├── cli.py                 # scripting CLI
├── api.py                 # REST API
├── src/                   # core, services, GUI mixins, utilities
├── tui/                   # Ratatui frontend
├── web/                   # FastAPI Web UI
├── tests/
├── packaging/
├── assets/
├── Dockerfile
└── docker-compose.yml
```

## Скриншоты

| Обработка | LLM | Настройки LLM |
|---|---|---|
| ![Обработка](https://i.postimg.cc/1XVNkf3V/Processing.png) | ![LLM](https://i.postimg.cc/7bvTwwCy/LLM-tab.png) | ![Настройки LLM](https://i.postimg.cc/c1PDr3Yk/LLM-settings.png) |

## Благодарности

- [SaluteDevices / GigaAM](https://github.com/salute-developers/GigaAM)
- [GigaAM-v3 on Hugging Face](https://huggingface.co/ai-sage/GigaAM-v3)
- [aystream / gigaam-mlx](https://github.com/aystream/gigaam-mlx)
- [NVIDIA Streaming Sortformer v2.1](https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2.1)
- [DeepFilterNet](https://github.com/Rikorose/DeepFilterNet) — MIT, optional neural noise suppression
