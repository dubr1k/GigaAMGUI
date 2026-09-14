# GigaAMLiquid for macOS

GigaAMLiquid is the native Swift/AppKit client for GigaAM Transcriber. It uses
the Python worker from this repository for model inference, media downloads and
export.

## Requirements

- macOS 13 or newer on Apple Silicon;
- Python 3.11;
- the Python dependencies from the repository.

## First launch from a release archive

Open Terminal in the extracted release directory and run:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip wheel "setuptools==81.0.0"
.venv/bin/python -m pip install -r requirements.txt
open GigaAMLiquid.app
```

The `.app` must remain beside `app.py` and the `src` directory. Alternatively,
set `GIGAAM_PROJECT_ROOT` to another prepared checkout and `GIGAAM_PYTHON` to
its Python executable.

## Offline release archive

The `GigaAMLiquid-macos-arm64-offline-*.zip` asset also contains
`GigaAMTranscriber.app` and the model files under `models/hf`. Keep these beside
`GigaAMLiquid.app`; the native client detects the frozen companion automatically
and does not require a separately installed Python environment or model
downloads.

The release app is ad-hoc signed, not notarized. On first launch macOS may ask
for confirmation in Privacy & Security.
