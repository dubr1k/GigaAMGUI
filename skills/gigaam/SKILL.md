---
name: gigaam
description: Transcribe audio/video to text or subtitles and summarise transcripts with the local GigaAM TUI (`gigaam transcribe`, `gigaam llm`). Use when asked to transcribe, recognise speech, make subtitles (SRT/VTT), diarize speakers, or summarise a recording or transcript.
---

# GigaAM (local speech-to-text)

`gigaam` is installed at `~/.local/bin/gigaam`. Never start plain `gigaam` (interactive TUI) from an agent — use the headless subcommands below. They print one line per finished file and exit non-zero on failure. The interactive TUI is Russian by default (`/lang en` switches it); the headless commands and their output are unchanged and always English.

## Transcribe

    gigaam transcribe FILE... [--output DIR] [--formats txt,txt_timecodes,srt,vtt,md,txt_diarize,txt_diarize_timecodes] [--diarize] [--speakers N|auto] [--diarization-backend pyannote|onnx|sortformer] [--backend auto|pytorch|mlx|onnx] [--model v3_e2e_rnnt|multilingual_ctc|multilingual_large_ctc] [--audio-mode auto|off|light|denoise] [--json] [--quiet]

- Defaults come from the user's saved settings (shared with the desktop app); flags override them.
- Results are written next to each input file unless `--output DIR` is given. Output: `✓ name.wav → /path/name.txt, /path/name.srt`.
- `--speakers` and `--diarization-backend` only take effect together with `--diarize`; the `sortformer` backend always detects the speaker count itself.
- `--json`: one JSON object per line on stdout (`started`, `file_started`, `progress`, `file_completed{result.saved_files}`, `completed{success}`); the worker's stderr is silenced (use human mode without `--quiet` to see model/download diagnostics). Prefer this when you need to parse results.
- The first run downloads the model (hundreds of MB); allow several minutes.

## Summarise / extract tasks with an LLM

    gigaam llm TRANSCRIPT.txt... --mode summary [--mode tasks] [--mode terms] [--mode custom --prompt "…"] [--output DIR] [--json]

Prints each mode's text under a `## <mode>` heading; also saves `session_llm_<mode>.txt` in DIR (default: next to the last transcript). Uses the provider configured in GigaAM settings (API or a CLI agent).

## MCP server (prefer when connected)

If a `gigaam` MCP server is connected (Claude Code `/mcp` lists it), call its tools instead of shelling out: `transcribe` (source `url` | `path` | `audio_base64`+`filename`; `format` `text|json|verbose|diarized|srt|vtt`; `diarize`, `num_speakers`, `diarization_backend`), `summarize` (`mode` `summary|tasks|terms|custom`), `list_models`, `list_llm_providers`, `server_status`; resources `gigaam://models`, `gigaam://status`; prompts `meeting_notes`, `subtitles_review`. Results come back as JSON in the tool result — no files to read. Long recordings take minutes: keep the call open (progress notifications arrive), do not retry. Full contract: the `gigaam-mcp` skill / `docs/MCP.md`.

Connect it once:

- Claude Code: `claude mcp add gigaam -- gigaam mcp` (local stdio; `path` to files on this machine works) or `claude mcp add --transport http gigaam https://gigaam-site.dubr1k.space/mcp --header "Authorization: Bearer <key>"` (remote; use `url` sources).
- Codex `~/.codex/config.toml`: `[mcp_servers.gigaam]` `command = "gigaam"`, `args = ["mcp"]`.
- Cursor `.cursor/mcp.json`: `{"mcpServers": {"gigaam": {"command": "gigaam", "args": ["mcp"]}}}`.

`gigaam mcp --http --port 8765` serves the same server as Streamable HTTP on `http://127.0.0.1:8765/mcp` behind an API key (`.api_keys`).

## Exit codes

0 success · 1 at least one file failed (see lines starting with `×` or `error:`) · 2 bad arguments or an input file that does not exist (message on stderr; nothing on stdout even with `--json`) · 3 worker unavailable → run `gigaam --update` and retry.
