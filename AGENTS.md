# AGENTS.md

Guidance for AI coding agents working in this repository. Human contributors will
find it useful too.

## What this project is

**GigaAM v3 Transcriber** — a Russian speech-to-text application built on the
[GigaAM](https://github.com/salute-developers/GigaAM) models. It ships in three
forms that all share the same processing core:

- **Desktop GUI** (PyQt6) — the primary product, packaged as portable binaries
  and a macOS `.app`.
- **Web UI** — a FastAPI backend + static frontend, deployed via Docker.
- **CLI** — batch transcription from the terminal.

Features: transcription to txt/md/srt/vtt (with timecodes and speaker
diarization), speaker diarization via `pyannote.audio`, LLM post-processing
(summary / tasks / custom prompt over OpenAI-compatible, Anthropic, or local
CLIs), media download from URLs (yt-dlp), and DOCX export.

## Entry points

| File | Purpose |
|------|---------|
| `app.py` | Desktop GUI launcher (PyQt6). Selects the PyTorch/MLX runtime **before** importing torch-heavy modules. |
| `web/web_app.py` | FastAPI web UI backend. **This is what Docker runs.** |
| `api.py` | A separate REST backend (rate-limited, zip export). Not used by Docker. |
| `cli.py` | Command-line batch transcription. |
| `download_models.py` | Pre-fetch model weights. |

## Layout

```
app.py, api.py, cli.py         entry points (kept at repo root)
packaging/                     PyInstaller .spec files + build_exe scripts
pyinstaller_hooks/             PyInstaller hooks (gigaam, docx, utf-8 runtime hook)
src/
  config.py                    central config (formats, backends, tokens, paths)
  core/                        processing pipeline
    processor.py               orchestrates convert → transcribe → diarize → export
    asr/                       ASR backends (pytorch / mlx)
    formatters.py              txt/md/srt/vtt rendering
    progress.py                progress events
  services/                    shared service layer (used by web AND api AND cli)
    transcription_service.py, llm_service.py, task_store.py,
    file_policy.py, health.py
  utils/                       audio_converter, diarization, media_downloader,
                               runtime_manager, pyannote_patch, logger, ...
  gui/                         PyQt6 desktop UI (see "GUI architecture")
  gigaam/                      vendored GigaAM package (avoid editing)
web/                           FastAPI app + static/ frontend
tests/                         pytest suite
Dockerfile, docker-compose.yml
.github/workflows/build.yml    CI: builds portable binaries for win/mac/linux
```

## Architecture rules

- **Layering:** `gui/`, `web/`, `api.py`, `cli.py` are *front-ends*. They depend
  on `src/services` and `src/core`, which depend on `src/utils`. Never import
  `src.gui` from the web/api/cli layers — the web layer is deliberately GUI-free
  so it runs headless.
- **GUI architecture:** the main window `GigaTranscriberQtApp` (`src/gui/app_qt.py`)
  is composed from **mixins** — `StyleMixin`, `ThemeMixin`, `UiBuildMixin`,
  `ProcessingMixin`, `FilesMixin`, `I18nMixin`, `SettingsMixin`, `DownloadMixin`,
  `LlmMixin`, `LlmUiMixin`. Each mixin's methods operate on `self` (the composed
  window). Keep every gui module ≤ ~600 lines; extract a mixin when one grows.
- **i18n:** UI strings are bilingual (ru/en). New user-facing widgets that show
  text must be (a) created with a default string and (b) retranslated in
  `i18n_mixin.py` (`_apply_language`). Prefer the `self._t("ru", "en")` helper in
  processing/logic code.
- **UI scaling:** never hard-code pixels/points in the GUI — use `self._px(n)` /
  `self._pt(n)` / `self._pt_css(n)` so the UI honours the display scale.

## Dev commands

```bash
# environment (Python 3.11+; 3.10 in Docker)
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
# GigaAM + (macOS) MLX are installed from git — see packaging/build_exe_mac.sh

# tests / lint
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m ruff check .

# run
.venv/bin/python app.py                 # desktop GUI
.venv/bin/python -m uvicorn web.web_app:app --port 8000   # web UI
```

Three GUI layout tests (`test_default_size_needs_no_scroll`,
`test_log_is_on_second_tab`, `test_speakers_spinbox_auto_value`) are
display/DPI-sensitive and may fail locally regardless of your change — verify
against a clean tree before assuming you broke them.

## Build & release

- Specs and build scripts live in `packaging/`. The `.spec` files derive
  `project_root` correctly whether run from root or elsewhere; always invoke
  PyInstaller **from the repo root**, e.g.
  `python -m PyInstaller packaging/gigaam_app_portable.spec --noconfirm`.
- **CI** (`.github/workflows/build.yml`) triggers on `v*` tags, builds the
  portable binary for Windows/macOS/Linux, downloads the correct per-OS static
  **ffmpeg** into `bin/` before building, and attaches artifacts to the release.
- macOS `.app`: `bash packaging/build_exe_mac.sh` (Apple Silicon; bundles torch + MLX).

## Gotchas (read before debugging packaging/runtime)

- **Lazy `src/gui/__init__.py`** loads `app_qt` via `importlib` so torch isn't
  imported before the runtime is chosen. Because of this, PyInstaller can't see
  `src.gui.app_qt` statically — every spec lists it in `hiddenimports`. If you
  add a new top-level GUI dependency chain, make sure it's reachable from
  `app_qt`'s imports or add it explicitly.
- **ffmpeg** is resolved by `src/utils/audio_converter.py`, which *validates the
  binary actually runs* (`ffmpeg -version`) and falls back to system PATH — a
  wrong-arch bundled binary is rejected, not fatal. `bin/` holds a committed
  macOS arm64 ffmpeg; Windows/Linux ffmpeg is provisioned by CI.
- **Diarization** needs an `HF_TOKEN` with `read` access **and** the user must
  accept the license for all pyannote models (segmentation-3.0, the wespeaker
  embedding model, speaker-diarization-3.1). Failures must surface the real
  cause — never fall back to labelling everything as one speaker.
- **DOCX** export needs `python-docx`'s bundled template; `pyinstaller_hooks/hook-docx.py`
  collects it. Keep `docx` in each spec's `hiddenimports` so the hook fires.
- **Docker** runs `web/web_app.py` (not `api.py`). The image excludes PyQt6.
  Set `COOKIE_SECURE=0` only when serving plain HTTP behind a proxy.

## Knowledge graph

This repo has a graphify knowledge graph in `graphify-out/` (gitignored). For
codebase questions prefer `graphify query "<question>"` /
`graphify explain "<concept>"` over broad grepping. After changing code, run
`graphify update .` to keep it current.

## Conventions

- Match the surrounding code's style, comment density (Russian comments are the
  norm here), and idioms.
- Only commit or push when explicitly asked. Branch off `main` for feature work.
- Keep generated artifacts (`build/`, `dist/`, `graphify-out/`, caches, logs) out
  of git — `.gitignore` already covers them.
