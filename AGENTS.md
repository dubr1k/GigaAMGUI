# AGENTS.md

Guidance for AI coding agents working in this repository. Human contributors will
find it useful too.

The big picture — layering, the rules the October 2026 refactor followed and
the roadmap of what is left — is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## What this project is

**GigaAM v3 Transcriber** — a Russian speech-to-text application built on the
[GigaAM](https://github.com/salute-developers/GigaAM) models. Every front-end
shares one Python processing core:

- **Desktop GUI** (PyQt6, `app.py`) — portable binaries for Windows/Linux and
  a macOS `.app` (`GigaAMTranscriber`).
- **GigaAM Liquid** (`macos/GigaAMLiquid`, Swift/AppKit) — the native macOS
  client; it bundles a headless Python worker (`GigaAMWorker.app`).
- **Terminal UI** (`tui/`, Rust/ratatui, binary `gigaam`) — installed from
  source by `scripts/install_tui.sh`, Homebrew or npm; ships with `main`, not
  with `v*` tags.
- **Web UI** (FastAPI `web/`) — the Docker product, with `/mcp` mounted.
- **REST API** (`api.py`, OpenAI-compatible `/v1/audio/transcriptions`) and
  **MCP server** (`src/mcp_server.py`, stdio or Streamable HTTP).
- **CLI** (`cli.py`) — batch transcription from the terminal.

Features: transcription to txt/md/srt/vtt (timecodes, speaker diarization via
pyannote, ONNX or Sortformer), Live capture with real-time recognition, LLM
post-processing (API providers or local agent CLIs), media download (yt-dlp),
DOCX export. The release version lives in `src/__init__.py` /
`packaging/_spec_common.py` (kept equal by `tests/test_release_hardening.py`).

## Entry points

| File | Purpose |
|------|---------|
| `app.py` | Desktop GUI launcher (PyQt6) and the smoke flags used by the build gates (`--selfcheck`, `--asr-runtime-smoke`, …). Activates the selected PyTorch runtime **before** importing torch-heavy modules. |
| `native_worker.py` | Frozen entry of `GigaAMWorker.app` for Liquid (`--native-worker` → `src.tui_worker`). |
| `src/tui_worker.py` | JSON-lines worker shared by the Rust TUI and Liquid (see "Worker protocol"). |
| `web/web_app.py` | FastAPI web UI. **This is what Docker runs** (`web.web_app:app`). |
| `api.py` | OpenAI-compatible REST API + `/mcp`. Not used by Docker. |
| `src/mcp_server.py` | MCP server (`gigaam mcp`, stdio or `--http`). |
| `cli.py` | Command-line batch transcription. |
| `download_models.py` | Pre-fetch model weights. |

## Layout

```
app.py, api.py, cli.py, native_worker.py, download_models.py   entry points
src/
  config.py, data_paths.py     configuration, data-directory selection
  core/                        processing pipeline (no front-end imports)
    processor.py               one file: convert → preprocess → ASR → diarize → export
                               (stages; diarization_stage.py, processing_support.py)
    export.py                  per-format atomic writes; formatters.py, subtitles.py
    progress.py                ProgressEvent + coerce_progress()/STAGE_LABELS for front-ends
    runtime_options.py         ASR backends, ONNX providers, bool parsing (dependency-free)
    model_loader.py, model_preparation.py, devices.py
    asr/                       backends (pytorch/mlx/onnx), longform.py (shared window
                               assembly + VAD cache), chunking.py (overlap stitching)
    diarization/               factory + pyannote/ONNX/Sortformer backends, mapping
  services/                    shared by web, api, cli, MCP and the worker
    transcription_service.py, transcription_api.py (REST/MCP core), mcp_backend.py,
    llm_service.py + cli_tools.py (provider registry), llm_worker_service.py,
    live_worker_service.py, tui_input_service.py, openai_errors.py/openai_stream.py,
    file_policy.py, task_store.py, health.py, api_keys.py
  live/                        Live capture: session.py (lifecycle façade), asr.py
                               (scheduler), timeline.py, mixing.py, recorder.py,
                               transcript.py, conversation.py, journal.py, capture/
  utils/                       audio_converter (ffmpeg), audio_preprocessing,
                               runtime_manager/torch_downloader (swappable torch),
                               model_cache, media_downloader, llm_client, logger, …
                               (utils/diarization.py is a re-export shim)
  gui/                         PyQt6 desktop UI (see "GUI architecture")
  gigaam/                      vendored GigaAM package (avoid editing)
web/                           web_app.py (assembly), state.py, task_registry.py,
                               auth.py (JWT, origin check, body guard), jobs.py,
                               routes/{auth,transcribe,tasks,llm,progress,pages}.py,
                               static/ (app.js, index.html)
tui/src/                       app/, commands/, settings/, cli/ (headless), i18n/,
                               ui/, worker_session.rs (process tree), signals.rs
macos/GigaAMLiquid/            Sources/GigaAMLiquid (App/, Pages/, Views/, UI/, jobs)
                               Sources/GigaAMLiquidCore (pure, unit-tested logic)
packaging/, pyinstaller_hooks/ PyInstaller specs, build scripts, hooks
scripts/                       build gates and tools (gitignored except `!` entries)
tests/                         pytest suite (conftest isolates the user profile)
Dockerfile, docker-compose.yml, .dockerignore (allowlist)
.github/workflows/             ci.yml (PRs), build.yml (v* tags), ui-screenshots.yml
```

## Architecture rules

- **Layering:** `gui/`, `web/`, `api.py`, `cli.py`, the worker and the MCP
  server are *front-ends*. They depend on `src/services` and `src/core`, which
  depend on `src/utils`; `src/core` never imports `src/services` or a
  front-end. Never import `src.gui` from the web/api/cli/worker layers — they
  run headless. Front-ends normalise progress with
  `src.core.progress.coerce_progress` and report failures from the processor's
  `result['error']` instead of inventing their own text.
- **GUI architecture:** the main window `GigaTranscriberQtApp` (`src/gui/app_qt.py`)
  is composed from **mixins**, one per surface or concern: shell and Processing
  page (`UiBuildMixin`, `ProcessingOptionsUiMixin`, `FilesMixin`,
  `ProcessingMixin`, `ResultViewMixin`, `DownloadMixin`), Live (`LiveMixin`
  session control, `LiveUiMixin` widgets/display, `LiveAssistantMixin` overlay
  and Q&A), LLM (`LlmMixin` logic, `LlmUiMixin` page, `LlmToolsMixin`,
  `LlmSettingsDialogMixin`), `JournalMixin`, `ApiSurfaceMixin`,
  `PreferencesMixin` (Settings tab), `MenuActionsMixin`, `SettingsMixin`
  (persistence), `StyleMixin` (palettes, metrics), `ThemeMixin` (+ the pure
  `qss.build_stylesheet`), `I18nMixin` and `LifecycleMixin`. Each mixin's
  methods operate on `self` (the composed window); a name may be defined in only
  one of them (`tests/test_gui_mixin_contract.py`). Keep every gui module
  ≤ ~600 lines; extract a mixin when one grows.
- **GUI busy state and exit:** `LifecycleMixin._busy_items()` is the one answer
  to "what is running" (batch, Live, download, LLM). Quit asks while anything
  runs, stops a Live session and waits (bounded) for its export; model/backend/
  device changes are refused while batch processing or Live hold the model.
  Long work (model load, device enumeration, `/health`, CLI scans) never runs
  on the Qt thread — results come back through `WorkerSignals`. An exception
  escaping a Qt slot is fatal in PyQt6, so slots that call into Live/LLM catch
  and report; `run_qt_app` also installs a logging `sys.excepthook`.
- **Single instance:** `src/gui/single_instance.py` (lock, open-files queue, argv
  parsing; stdlib + `src.config` only) is shared by `app.py` and `app_qt`;
  `src/gui/application.py` holds `GigaApplication`, which `app.py` must create
  so Finder/Dock `FileOpen` events reach the window.
- **i18n:** UI strings are bilingual (ru/en). Register a static caption once with
  `self._bilingual(widget.setText, "ru", "en")` (any setter: tooltip, title,
  placeholder, combo item via a lambda); captions that change at runtime belong
  in the surface's `_retranslate_*` method next to its builder (`_apply_language`
  only dispatches). Prefer `self._t("ru", "en")` in processing/logic code,
  dialog titles and messages. `tests/test_gui_english_ui.py` fails on any
  Russian text left after switching to English.
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

## Worker protocol (TUI and Liquid)

`src/tui_worker.py` speaks JSON lines on stdin/stdout. Both clients ship
separately (the TUI from `main`, Liquid bundled with its worker), so **every
change must be additive**: new optional fields and new message types only;
never rename or remove one.

- The client may open with `{"type":"hello","client":"tui"|"liquid",
  "features":[…]}`; the worker answers `ready` with `protocol_version` and
  `capabilities` (including `compact_completed`). `client:"tui"` compacts
  `file_completed`/`completed`; `features:["compact_completed"]` compacts only
  `completed`.
- Every `error` raised for a command carries `"command": "<type>"` (Live:
  `live_start`, `live_ask`, …). Clients classify errors by it and fall back to
  message text only for old workers.
- `cancel` interrupts the current file (the processor's `cancel_check`): an
  interrupted file gets `file_started` but no `file_completed` and no entry in
  `completed.results`; clients show it as interrupted, not failed.
- Output is always UTF-8 and strict JSON (no NaN; undecodable file names become
  U+FFFD); `PYTHONUTF8`/`PYTHONIOENCODING` are set by the TUI as well.
- Tests: `tests/test_tui_worker.py`, `tests/fixtures/tui_worker_stub.py` (PTY
  tests drive the real TUI binary), Swift decoder fixtures captured from a real
  worker in `GigaAMLiquidCore` tests.

## Dev commands

```bash
# environment (Python 3.11+; 3.10 in Docker)
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
# GigaAM + (macOS) MLX are installed from git — see packaging/build_exe_mac.sh

# tests / lint (never bare `ruff check .`: untracked offline/ and dist/ hold
# unpacked bundles with vendored code)
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/ -q
.venv/bin/python -m ruff check src tests web api.py cli.py app.py scripts
cargo test --locked --manifest-path tui/Cargo.toml          # + clippy -D warnings
swift test --package-path macos/GigaAMLiquid                # retry once on a
                                                            # "TestingMacros" plugin error
# PTY tests of the TUI run when GIGAAM_TUI_TEST_BINARY points at a built binary.

# run
.venv/bin/python app.py                 # desktop GUI
.venv/bin/python -m uvicorn web.web_app:app --port 8000   # web UI
```

`tests/conftest.py` points `GIGAAM_CONFIG_DIR` at a temporary directory, so the
suite never writes logs or statistics into the real user profile. PR CI
(`ci.yml`) runs ruff, the packaging/release contract tests (text-only, no
heavy dependencies), the TUI (fmt, clippy, tests, PTY tests, a Windows build)
and the Swift build/tests; the full ML suite runs in `build.yml` on tags.
`tests/test_gui_live_layout.py::test_live_device_combos_are_usable` is
display/DPI-sensitive — verify against a clean tree before assuming you broke it.

## Build & release

- Specs and build scripts live in `packaging/`. The `.spec` files derive
  `project_root` correctly whether run from root or elsewhere; always invoke
  PyInstaller **from the repo root**, e.g.
  `python -m PyInstaller packaging/gigaam_app_portable.spec --noconfirm`.
- **CI** (`.github/workflows/build.yml`) triggers on `v*` tags, builds the
  portable binary for Windows/macOS/Linux, downloads the correct per-OS static
  **ffmpeg** into `bin/` before building, and attaches artifacts to the release.
- macOS `.app`: `bash packaging/build_exe_mac.sh` (Apple Silicon; full PyInstaller
  bundle with PyTorch/MLX resources). Liquid is installed locally with
  `scripts/install_liquid_local.sh` (see CLAUDE.md). The version is bumped in
  `packaging/_spec_common.py` (`APP_VERSION`, and `APP_BUILD_VERSION` =
  MAJOR.MINOR.(PATCH*10 + rebuild)) plus the other spots listed in
  `tests/test_release_hardening.py`.
- **Build gates.** The hooks and verifiers refuse a bundle that would only fail
  on the user's machine: `hook-gigaam.py` stops on a namespace-only `gigaam`,
  release specs collect packages with `collect_required()` (no silent
  `[skip]`), `scripts/verify_macos_bundle.py` checks the frozen PYZ for the
  model code (`--modules-only` for the worker), `--selfcheck` imports
  `gigaam.model`, `scripts/check_site_packages.py` rejects Syncthing debris in
  the build venv, and `scripts/check_torch_triple.py` pins the build-time torch
  triple. Pins shared by several files are kept equal by
  `tests/test_pin_consistency.py`.
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
- **Docker** runs `web/web_app.py` (not `api.py`). The build context is an
  allowlist (`.dockerignore`): add new runtime directories there explicitly.
  The image excludes PyQt6. Set `COOKIE_SECURE=0` only when serving plain HTTP
  behind a proxy. Web protection settings (`WEB_ALLOW_CLIENT_LLM_CLI`,
  `WEB_TRUSTED_ORIGINS`, `WEB_LOGIN_RATE_LIMIT`, …) must also be listed in
  `docker-compose.yml`, whose environment is explicit.
- **Syncthing** syncs this tree, `.git` and `.venv` included. Conflict copies
  (`*.sync-conflict-*`) inside `site-packages` silently replace packages or
  their metadata; run `scripts/check_site_packages.py` when an import behaves
  oddly. `refs/stash` is shared by every git worktree of the repo — do not use
  `git stash` from agent worktrees.
- **macOS git ignores case:** the `models/` rule in `.gitignore` also matches a
  `Models/` source folder; a pytest guard fails if a Swift source is ignored.

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
