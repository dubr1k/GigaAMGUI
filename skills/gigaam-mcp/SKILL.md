---
name: gigaam-mcp
description: Use the GigaAM MCP server (tools `transcribe`, `summarize`, `list_models`, `list_llm_providers`, `server_status`) to turn audio/video into text, subtitles (SRT/VTT) or speaker-labelled segments and to summarise transcripts with an LLM — Russian speech-to-text. Use when asked to transcribe, recognise speech, make subtitles, diarize speakers, summarise a recording or meeting, or when a `gigaam` MCP server is connected (locally via `gigaam mcp` or remotely at https://gigaam-site.dubr1k.space/mcp).
---

# GigaAM over MCP

The `gigaam` MCP server runs GigaAM v3 (Russian ASR) either on this machine (stdio, started by the client as `gigaam mcp`) or remotely (Streamable HTTP, `https://gigaam-site.dubr1k.space/mcp`, `Authorization: Bearer <key>` on every request). Prefer its tools over shelling out; nothing needs to be installed locally. Recognition runs at roughly real-time speed — keep the call open and never retry a slow `transcribe`.

**Local stdio lifecycle:** connecting does not load ASR weights. Each `transcribe` loads a request-owned model and releases it after processing, including failure. The MCP process stays connected, but does not retain ASR weights between calls. `server_status`, `list_models` and `summarize` do not load ASR. `asr.loader_loaded=false` with `asr.error=null` is normal for this local mode: call `transcribe` directly, without preloading. That flag describes the resident loader, not in-flight request models; use `busy.active` and progress for running jobs. HTTP/REST/web servers keep their existing resident-model lifecycle.

## Connecting (if the server is not listed yet)

- Claude Code, local: `claude mcp add gigaam -- gigaam mcp`; remote: `claude mcp add --transport http gigaam https://gigaam-site.dubr1k.space/mcp --header "Authorization: Bearer gam_..."`.
- Codex `~/.codex/config.toml`: `[mcp_servers.gigaam]` with `command = "gigaam"`, `args = ["mcp"]` (local) or `url = "https://gigaam-site.dubr1k.space/mcp"`, `http_headers = { Authorization = "Bearer gam_..." }`, `tool_timeout_sec = 3600` (remote).
- Cursor `.cursor/mcp.json`: `{"mcpServers": {"gigaam": {"command": "gigaam", "args": ["mcp"]}}}` or `{"url": "...", "headers": {"Authorization": "Bearer gam_..."}}`.

## Tools

`transcribe` — exactly one source: `url` | `path` | `audio_base64` (+ `filename`).

| Parameter | Default | Notes |
|---|---|---|
| `url` | — | http(s) file or a page yt-dlp understands; the server downloads it. Use for anything remote. |
| `path` | — | File on the **server's** filesystem. Only with a local (stdio) server, or when the operator set `GIGAAM_MCP_ALLOW_PATHS=1`; remote servers answer `[paths_not_allowed]`. |
| `audio_base64`, `filename` | — | Inline file for short clips only; must fit `server_status().limits.max_inline_mb` (25 MB default). `filename` needs a media extension. |
| `model` | `v3_e2e_rnnt` | Or an alias (`whisper-1`, `gigaam`); see `list_models`. |
| `language` | `ru` | Echoed into the result; GigaAM recognises Russian. |
| `format` | `json` | `text` / `json` (same object: text + duration + usage), `verbose` (`segments`, `words` with `word_timestamps=true`), `diarized` (`segments` with `speaker` A/B/…), `srt` / `vtt` (`subtitles` string). |
| `word_timestamps` | `false` | Per-word timestamps in `verbose`. |
| `diarize` | `false` | Speaker labels; implied by `format="diarized"`. |
| `diarization_backend` | `pyannote` | `pyannote` needs `HF_TOKEN` on the server; `sortformer` and `onnx` do not. |
| `num_speakers` | — | Integer ≥ 1; not allowed with `sortformer`. |
| `audio_preprocessing` | server setting | `off` / `auto` / `light` / `denoise`. |
| `asr_backend`, `onnx_provider` | server setting | Engine override (`torch`, `onnx`; provider `cpu`, `cuda`, …). |

Returns `{text, duration, language, usage: {type: "duration", seconds}, source: {kind, name}}` plus `segments` / `words` / `diarization: {requested, applied}` / `subtitles` depending on `format`. If `diarization.applied` is `false`, speaker labels are placeholders.

`summarize(text, mode="summary"|"tasks"|"terms"|"custom", prompt=None, provider=None, model=None)` — LLM post-processing with the server's configured provider. `prompt` is required for `custom`; `provider` is one of `API`, `Claude Code`, `Codex`, `OpenCode`, `Pi`, `oh-my-pi`, `Other` (check `list_llm_providers` first). Returns `{mode, provider, model, answer}`.

`list_models()` — model ids, aliases, backends, ONNX providers, active selection. `list_llm_providers()` — which CLI providers are installed and whether an API key is configured. `server_status()` — `{version, runtime, asr, busy: {active, max}, limits: {max_file_mb, max_inline_mb, max_concurrent}}`; `busy.active` counts every job holding the server's shared slots (REST and web-panel jobs included, not only MCP calls).

Resources (JSON): `gigaam://models`, `gigaam://status`. Prompts: `meeting_notes` (`transcript`, `language="ru"|"en"`) → meeting minutes (decisions, action items, open questions); `subtitles_review` (`srt`) → phrase-break / punctuation review of SRT.

## Choosing the source

1. Remote server → `url` (direct file links, YouTube and other yt-dlp pages). Do not upload local files as base64 unless they are a short clip.
2. Local stdio server → `path` (absolute, `~` allowed). Also on a remote server when `server_status` / the operator says `GIGAAM_MCP_ALLOW_PATHS=1` (files must then be under `GIGAAM_MCP_PATH_ROOT`).
3. `audio_base64` only for clips well under `limits.max_inline_mb`; anything longer gets `[file_too_large]`.

Supported extensions: mp3 wav m4a aac mp4 avi mov mkv webm flac ogg wma qta 3gp.

## Long calls

`transcribe` takes minutes for long recordings and sends progress notifications (stage `downloading` → `preparing` → `conversion` → `preprocessing` → `transcription` → `diarization`, percent 0–100). Keep the request open; set the client tool timeout to ≥ 1 h (Claude Code: `MCP_TOOL_TIMEOUT`, Codex: `tool_timeout_sec`). Retrying after a timeout starts a second job and takes another concurrency slot. Check `server_status().busy` before big batches; call `transcribe` sequentially rather than in parallel.

## Errors

Tool errors contain a bracketed code — match `[code]` anywhere in the text, not at the start (the transport prefixes it: `Error executing tool transcribe: [file_too_large] ...`):

- `[invalid_request]` — not exactly one source, `audio_base64` without `filename` / not base64, empty `text`. Fix the arguments.
- `[unsupported_parameter]` — bad `diarization_backend`, `audio_preprocessing`, `mode`, `provider`, `num_speakers` (< 1 or with `sortformer`), `asr_backend`/`onnx_provider` pair. Use values from `list_models` / `list_llm_providers`.
- `[model_not_found]` — unknown `model`; `[model_not_loaded]` — ASR not loaded on the server (see `server_status().asr`).
- `[diarization_unavailable]` — `pyannote` without `HF_TOKEN`; retry with `diarization_backend="sortformer"` or `"onnx"`.
- `[unsupported_file]` — extension not supported; `[file_too_large]` — over `max_file_mb` (url/path) or `max_inline_mb` (base64): switch to `url`/`path` or split the file.
- `[file_not_found]` — `path` missing on the server; `[paths_not_allowed]` — `path` on a remote server: use `url`/`audio_base64`; `[path_outside_root]` — move the file under the allowed root.
- `[download_failed]` — the URL could not be fetched (message has the reason); verify the link.
- `[prompt_required]` — `mode="custom"` needs `prompt`; `[llm_failed]` — the provider failed (no key, CLI error): pick another via `list_llm_providers`.
- `[processing_failed]`, `[internal_error]` — server-side failure; report it with the server log, do not retry blindly.
- HTTP 401 `invalid_api_key` (remote) — missing/wrong `Authorization: Bearer <key>` header; 503 — the server is still starting.

## Recipes

- **Transcript → SRT**: `transcribe(url=..., format="srt")` → `subtitles`; optionally run prompt `subtitles_review(srt=subtitles)` and apply the fixes.
- **Meeting → notes**: `transcribe(url=..., format="diarized", num_speakers=N)` → join `segments` as `Speaker: text` lines → `summarize(text, mode="tasks")` for action items, or prompt `meeting_notes(transcript, language="ru")` to write the minutes yourself.
- **Before a big job**: `server_status()` → check `busy.active < busy.max`, `limits`, `asr.error`; a local stdio server need not have `asr.loader_loaded=true`. Use `list_models()` if a specific model or backend is needed.
- **Custom extraction**: `summarize(text, mode="custom", prompt="Выпиши все названные суммы и даты")`.
