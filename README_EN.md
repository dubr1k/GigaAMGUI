# GigaAM v3 Transcriber

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![Desktop: PyQt6](https://img.shields.io/badge/Desktop-PyQt6-41CD52)](https://www.riverbankcomputing.com/software/pyqt/)
[![API: FastAPI](https://img.shields.io/badge/API-FastAPI-009688)](https://fastapi.tiangolo.com/)
[![Web: Docker](https://img.shields.io/badge/Web-Docker-2496ED)](https://www.docker.com/)
[![GitHub stars](https://img.shields.io/github/stars/dubr1k/GigaAMGUI?style=social)](https://github.com/dubr1k/GigaAMGUI/stargazers)

[🇷🇺 Русская версия](README.md) · **🇺🇸 English**

Russian speech-to-text transcription for audio and video powered by **GigaAM-v3**. One shared service layer with five interfaces: Desktop GUI, CLI, REST API, Web GUI, and terminal TUI.

> GigaAM Transcriber is a complete workflow for transcription, export, diarization, and LLM post-processing—not just a model wrapper.

## Contents

- [Features](#features)
- [Quick start](#quick-start)
- [Interfaces](#interfaces)
- [Configuration](#configuration)
- [ASR backend](#asr-backend)
- [Project layout](#project-layout)
- [Screenshots](#screenshots)
- [Credits](#credits)

## Features

- Batch processing, recursive folder scans, drag & drop, and media downloads through `yt-dlp`.
- Export to `txt`, `txt_timecodes`, `txt_diarize`, `txt_diarize_timecodes`, `md`, `srt`, and `vtt`.
- Selectable diarization through `pyannote` or NVIDIA Streaming Sortformer v2.1.
- MLX RNN-T on Apple Silicon; CPU, CUDA, Intel XPU, and MPS support.
- LLM summaries, action items, and custom prompts.
- OpenAI-compatible API, Claude Code, Codex, OpenCode, Pi, and arbitrary CLI LLM providers.
- RU/EN, light/dark themes, logs, stage-aware progress, and queue cancellation.
- Authenticated Web UI with SSE progress, restored tasks, and Docker hardening.

## Quick start

### 1. Install dependencies

```bash
git clone https://github.com/dubr1k/GigaAMGUI.git
cd GigaAMGUI
cp .env.example .env
python -m pip install -r requirements.txt
ffmpeg -version
```

### 2. Set a Hugging Face token

```env
HF_TOKEN=your_huggingface_token_here
```

For diarization, accept the terms for `pyannote/speaker-diarization-3.1` and `pyannote/segmentation-3.0`.

### Optional: NVIDIA Sortformer

Sortformer is installed separately so the heavy NeMo stack does not inflate the
default installation:

```bash
python -m pip install -r requirements-sortformer.txt
python cli.py --diarize --diarization-backend sortformer -f audio.wav
```

This uses `nvidia/diar_streaming_sortformer_4spk-v2.1` with the official
high-latency model-card settings. The model auto-detects active
speakers, supports at most four voices, and does not require `HF_TOKEN`. CUDA is
recommended; CPU is much slower. NeMo does not support MPS, so it falls back to
CPU. The model (~471 MB) is downloaded on first use. The Space's NeMo 2.5.3 pin
is intentionally avoided because later releases fix known vulnerabilities; the
optional requirements file pins the reviewed 2.7 branch.
For the Web UI, build the extended image with
`INSTALL_SORTFORMER=1 docker compose build gigaam-web`.

## Interfaces

| Interface | Start | Use case |
|---|---|---|
| Desktop GUI | `python app.py` | Regular interactive work |
| CLI | `python cli.py -f audio.wav -o output` | Scripts and automation |
| REST API | `python api.py` | Integrations; docs at `http://127.0.0.1:8000/docs` |
| Web GUI | `docker compose up -d --build gigaam-web` | Local web panel at `http://127.0.0.1:8001/` |
| TUI *(preview)* | `cd tui && cargo run --release` | Interactive terminal queue |

### TUI

```bash
curl -fsSL https://raw.githubusercontent.com/dubr1k/GigaAMGUI/main/scripts/install_tui.sh | bash
gigaam
```

## Configuration

For the Web UI, add these values to `.env`:

```env
WEB_SECRET=change_me
WEB_USERNAME=admin
WEB_PASSWORD=replace_with_strong_password
```

For RTX 50xx / Blackwell, install a compatible PyTorch build first:

```bash
python -m pip install torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
python -m pip install -r requirements.txt
```

## ASR backend

On macOS Apple Silicon, `auto` uses [gigaam-mlx](https://github.com/aystream/gigaam-mlx) and falls back to PyTorch when necessary. Other platforms use PyTorch.

```bash
python cli.py --backend auto -f audio.wav
python cli.py --backend mlx -f audio.wav
python cli.py --backend pytorch -f audio.wav
```

MLX is used only for ASR; diarization always uses PyTorch.

## Project layout

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

## Screenshots

| Processing | LLM | LLM settings |
|---|---|---|
| ![Processing](https://i.postimg.cc/KYbrtrvN/Processing.png) | ![LLM](https://i.postimg.cc/wjMdgLpK/LLM-tab.png) | ![LLM settings](https://i.postimg.cc/K8jyxByH/LLM-settings.png) |

## Credits

- [SaluteDevices / GigaAM](https://github.com/salute-developers/GigaAM)
- [GigaAM-v3 on Hugging Face](https://huggingface.co/ai-sage/GigaAM-v3)
- [aystream / gigaam-mlx](https://github.com/aystream/gigaam-mlx)
