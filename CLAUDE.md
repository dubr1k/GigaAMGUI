# CLAUDE.md

Instructions for Claude Code (and other AI agents) in this repository.

**The full project guide is [AGENTS.md](./AGENTS.md) — read it first.** This file
adds Claude-specific workflow notes; everything in AGENTS.md applies here too.

## Quick orientation

GigaAM v3 Transcriber — a Russian speech-to-text app (PyQt6 desktop + FastAPI web
+ CLI) built on the GigaAM models. Front-ends (`src/gui`, `web`, `api.py`,
`cli.py`) sit on a shared `src/services` + `src/core` + `src/utils` stack. The
web/api/cli layers must never import `src.gui`.

## Working here

- **Knowledge graph first.** `graphify-out/` holds a graph of this codebase. For
  architecture/relationship questions run `graphify query "<question>"`,
  `graphify path "<A>" "<B>"`, or `graphify explain "<concept>"` before grepping —
  it returns a scoped subgraph. After you modify code, run `graphify update .`
  (AST-only, no API cost) to keep it current.
- **Tests & lint:** `.venv/bin/python -m pytest tests/ -q` and
  `.venv/bin/python -m ruff check .`. Three DPI-sensitive GUI layout tests can
  fail independently of your change — compare against a clean tree first.
- **Verify UI changes visually.** GUI code renders through per-widget + global
  QSS and honours display scale via `self._px()/_pt()`. Rendering a widget
  offscreen (`QT_QPA_PLATFORM=offscreen`, `widget.grab().save(...)`) and
  inspecting the PNG is a reliable way to confirm a visual fix.
- **Don't hard-code sizes or strings.** Use `self._px()/_pt()/_pt_css()` for
  metrics and add bilingual (ru/en) text via `i18n_mixin._apply_language` /
  `self._t()`.
- **Packaging lives in `packaging/`.** Run PyInstaller from the repo root
  (`python -m PyInstaller packaging/<spec> --noconfirm`). Releases are cut by
  tagging `v*`, which triggers `.github/workflows/build.yml`.

## Guardrails

- Commit / push / merge / tag only when explicitly asked; branch off `main`.
- Keep generated artifacts out of git (`.gitignore` already covers `build/`,
  `dist/`, `graphify-out/`, caches, logs, model files).
- When touching diarization, ffmpeg, DOCX, or the PyInstaller specs, re-read the
  **Gotchas** section of AGENTS.md — those areas have non-obvious failure modes.
