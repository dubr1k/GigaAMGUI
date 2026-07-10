# GigaAM v3 Transcriber

[🇷🇺 Русская версия](README.md) · **🇺🇸 English**

Russian speech-to-text transcription for audio and video with **Desktop GUI**, **CLI**, **REST API**, and hardened **Web GUI** powered by **GigaAM-v3**.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![PyQt6](https://img.shields.io/badge/Desktop-PyQt6-41CD52)
![FastAPI](https://img.shields.io/badge/API-FastAPI-009688)
![Docker](https://img.shields.io/badge/Web-Docker-2496ED)
![Stars](https://img.shields.io/github/stars/dubr1k/GigaAMGUI?style=social)

</div>

---

## Screenshots

### Processing

![Processing](https://i.postimg.cc/KYbrtrvN/Processing.png)

### LLM tab

![LLM tab](https://i.postimg.cc/wjMdgLpK/LLM-tab.png)

### LLM settings

![LLM settings](https://i.postimg.cc/K8jyxByH/LLM-settings.png)

---

## What it is

**GigaAM v3 Transcriber** is a full transcription workflow around GigaAM-v3, not just a model wrapper.

## Features

- Desktop GUI, CLI, REST API, and Web GUI
- Batch processing for files and folders
- Recursive folder scan and drag & drop
- Media download via `yt-dlp`
- Speaker diarization via `pyannote`
- Export to `txt`, `txt_timecodes`, `txt_diarize`, `txt_diarize_timecodes`, `md`, `srt`, `vtt`
- Built-in LLM tab for:
  - summaries
  - tasks / action items
  - custom prompts
- LLM providers:
  - OpenAI-compatible API
  - Claude Code
  - Codex
  - OpenCode
  - Pi
  - arbitrary external CLI
- RU/EN interface switch
- Theme switch
- Single-instance app behavior
- Processing log, progress tracking, cancel support
- Web GUI with auth, task history restore, SSE progress, Docker hardening
- CPU, CUDA, Intel XPU, Apple Silicon MPS support

## Quick start

```bash
git clone https://github.com/dubr1k/GigaAMGUI.git
cd GigaAMGUI
cp .env.example .env
pip install -r requirements.txt
```

Minimum `.env`:

```env
HF_TOKEN=your_huggingface_token_here
```

For Web GUI:

```env
WEB_SECRET=change_me
WEB_USERNAME=admin
WEB_PASSWORD=strong_password
```

Check FFmpeg:

```bash
ffmpeg -version
```

## Run

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

Endpoints:

- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/redoc`
- `http://127.0.0.1:8000/health`

Web GUI:

```bash
docker compose up -d --build gigaam-web
```

Default:

- `http://127.0.0.1:8001/`
- `http://127.0.0.1:8001/health`

## Requirements

- Python 3.10+
- FFmpeg in `PATH`
- HuggingFace token
- For diarization, accept terms for:
  - `pyannote/speaker-diarization-3.1`
  - `pyannote/segmentation-3.0`

## RTX 50xx / Blackwell

```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements.txt
```

## Project structure

```text
GigaAMGUI/
├── app.py
├── cli.py
├── api.py
├── web/
├── src/
├── tests/
├── Dockerfile
├── docker-compose.yml
├── BUILD_INSTRUCTIONS.md
└── assets/screenshots/
```

## Credits

- [SaluteDevices / GigaAM](https://github.com/salute-developers/GigaAM)
- [GigaAM-v3 on HuggingFace](https://huggingface.co/ai-sage/GigaAM-v3)
