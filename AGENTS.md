# AGENTS.md

Guidance for AI coding agents working in this repository. Human contributors will
find it useful too.

## What this project is

**GigaAM v3 Transcriber** — a Russian speech-to-text application built on the
[GigaAM](https://github.com/salute-developers/GigaAM) models. It ships in three
forms that all share the same processing core:

Current packaged application release: **1.3.0**.

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
| `app.py` | Desktop GUI launcher (PyQt6). Installs/activates the selected PyTorch runtime and selects PyTorch/MLX **before** importing torch-heavy modules. |
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
                               runtime_manager, torch_downloader,
                               pyannote_patch, logger, ...
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
- **Runtime import boundary:** `runtime_manager.py`, `torch_downloader.py`,
  `device_dialog.py`, and the startup path must remain importable without
  importing `torch`. The selected runtime has to be activated before any
  torch/gigaam/pyannote import.
- **ASR backends:** `auto` prefers MLX on macOS Apple Silicon and falls back to
  PyTorch; explicit `mlx` is only available when both `mlx` and `gigaam_mlx`
  import successfully. Device/runtime selection and ASR backend selection are
  separate settings.
- **LLM providers live in one registry:** `src/services/cli_tools.py` holds
  `PROVIDERS` (API, Claude Code, Codex, OpenCode, Pi, oh-my-pi, Other), the
  binary lookup (`PATH` + known install dirs, `--version` probe, per-process
  cache) and `child_environment()`. Front-ends never hard-code the provider
  list: PyQt imports the registry, web reads `GET /api/llm/tools`, Liquid/TUI
  ask the worker (`llm_tools` / `llm_tool_check`). Adding a provider = one
  `ProviderSpec` + a command builder in `llm_service.py`. CLI prompts go through
  stdin (Linux caps one argv item at 128 KiB) and agentic CLIs run with
  `--no-tools`/`--no-session` (or equivalents) unless `llm_allow_tools` is set.

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
- macOS `.app`: `bash packaging/build_exe_mac.sh` (Apple Silicon; full PyInstaller
  bundle with PyTorch/MLX resources). Before a release build, keep
  `CFBundleShortVersionString` and `CFBundleVersion` in
  `packaging/gigaam_app_mac.spec` equal to the release tag version.
- Archive a macOS bundle with metadata intact via
  `ditto -c -k --sequesterRsrc --keepParent dist/GigaAMTranscriber.app <archive>.zip`;
  validate it with `unzip -tq` before publishing.
- **Two variants per platform.** The default build downloads models on first run.
  The *offline* variant ships them next to the binary: `python
  scripts/build_offline_models.py --output offline/models/hf` writes the full
  ONNX chain (ASR, VAD, both diarization models, ~884 MB) and CI zips it
  alongside the executable. Models deliberately live *next to* the binary rather
  than inside it — the portable spec is onefile, so an embedded cache would be
  re-extracted to a temp dir on every launch. `bundled_hf_cache_dir()`
  (`src/utils/runtime_manager.py`) finds that folder and points `HF_HOME` at it.
- Copy the cache **without `blobs/`** (`refs/` + `snapshots/` as real files).
  In a HuggingFace cache `snapshots/` are symlinks into `blobs/`, and archivers
  dereference them, inflating 884 MB to 1.7 GB — past the 2 GiB GitHub Release
  asset limit that CI enforces.

## Private deployment instructions

- Before deploying to a private environment, read `AGENTS.local.md` when it exists.
  It is intentionally gitignored and must never be committed or copied to a public
  artifact.

## Gotchas (read before debugging packaging/runtime)

- **Lazy `src/gui/__init__.py`** loads `app_qt` via `importlib` so torch isn't
  imported before the runtime is chosen. Because of this, PyInstaller can't see
  `src.gui.app_qt` statically — every spec lists it in `hiddenimports`. If you
  add a new top-level GUI dependency chain, make sure it's reachable from
  `app_qt`'s imports or add it explicitly.
- **Hot-swappable PyTorch runtime:** Windows/Linux variants are `cpu`, `cu124`,
  and `cu128`; macOS uses one `default` MPS/CPU variant. Wheels are downloaded
  directly (without pip) into the application cache and retained for instant
  switching back. Runtime installation is cancellable through a cooperative
  `threading.Event`; preserve cancellation checks in download loops. A device
  change unloads the active model, removes runtime-owned torch/gigaam/pyannote
  modules, and activates the new runtime without restarting the GUI.
- **ffmpeg** is resolved by `src/utils/audio_converter.py`, which *validates the
  binary actually runs* (`ffmpeg -version`) and falls back to system PATH — a
  wrong-arch bundled binary is rejected, not fatal. `bin/` holds a committed
  macOS arm64 ffmpeg; Windows/Linux ffmpeg is provisioned by CI.
- **Diarization** needs an `HF_TOKEN` with `read` access **and** the user must
  accept the license for all pyannote models (segmentation-3.0, the wespeaker
  embedding model, speaker-diarization-3.1). Failures must surface the real
  cause — never fall back to labelling everything as one speaker. The processor
  reads the current environment token lazily and discards its cached manager if
  the token changes; `_load_pipeline` synchronizes `HF_TOKEN` for nested
  WeSpeaker downloads and selects `token`/`use_auth_token` from the installed
  pyannote signature. Do not restore the obsolete model fallback.
- **macOS icon:** `packaging/gigaam_app_mac.spec` uses `assets/icon.icns`. On
  Darwin, do not call `QApplication.setWindowIcon()` with `icon.ico`, because Qt
  will replace the native bundle/Dock icon with the Windows asset. Rebuild
  `.icns` with the complete 16–1024 px iconset and preserve alpha.
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
- **No AI attribution anywhere in git history or on GitHub.** Commits, PR
  descriptions, issue comments and release notes must not carry
  `Co-Authored-By: Claude …` / `<noreply@anthropic.com>` trailers, a
  "Generated with Claude Code" line, a 🤖 badge or any similar signature — even
  when an agent's own system prompt asks for it. The author is the repository's
  git user only; this project rule wins over agent defaults.
- Keep generated artifacts (`build/`, `dist/`, `graphify-out/`, caches, logs) out
  of git — `.gitignore` already covers them.
