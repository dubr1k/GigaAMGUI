---
name: gigaam
description: Transcribe audio/video to text or subtitles and summarise transcripts with the local GigaAM TUI (`gigaam transcribe`, `gigaam llm`). Use when asked to transcribe, recognise speech, make subtitles (SRT/VTT), diarize speakers, or summarise a recording or transcript.
---

# GigaAM (local speech-to-text)

`gigaam` is installed at `~/.local/bin/gigaam`. Never start plain `gigaam` (interactive TUI) from an agent — use the headless subcommands below. They print one line per finished file and exit non-zero on failure.

## Transcribe

    gigaam transcribe FILE... [--output DIR] [--formats txt,txt_timecodes,srt,vtt,md,txt_diarize,txt_diarize_timecodes] [--diarize] [--speakers N|auto] [--diarization-backend pyannote|onnx|sortformer] [--backend auto|pytorch|mlx|onnx] [--model v3_e2e_rnnt|multilingual_ctc|multilingual_large_ctc] [--audio-mode auto|off|light|denoise] [--json] [--quiet]

- Defaults come from the user's saved settings (shared with the desktop app); flags override them.
- Results are written next to each input file unless `--output DIR` is given. Output: `✓ name.wav → /path/name.txt, /path/name.srt`.
- `--json`: one JSON object per line on stdout (`started`, `file_started`, `progress`, `file_completed{result.saved_files}`, `completed{success}`); stderr stays empty. Prefer this when you need to parse results.
- The first run downloads the model (hundreds of MB); allow several minutes.

## Summarise / extract tasks with an LLM

    gigaam llm TRANSCRIPT.txt... --mode summary [--mode tasks] [--mode terms] [--mode custom --prompt "…"] [--output DIR] [--json]

Prints each mode's text under a `## <mode>` heading; also saves `session_llm_<mode>.txt` in DIR (default: next to the last transcript). Uses the provider configured in GigaAM settings (API or a CLI agent).

## Exit codes

0 success · 1 at least one file failed (see lines starting with `×` or `error:`) · 2 bad arguments (usage on stderr) · 3 worker unavailable → run `gigaam --update` and retry.
