# GigaAMLiquid for macOS

GigaAMLiquid is the native Swift/AppKit client for GigaAM Transcriber. Release
builds are self-contained: the frozen Python worker (`GigaAMTranscriber.app`)
that performs model inference, media downloads and export is embedded inside
`GigaAMLiquid.app/Contents/Resources`, so the archive ships a single application.

## Requirements

- macOS 13 or newer on Apple Silicon.

## First launch from a release archive

Extract the archive and open the only application inside it:

```bash
open GigaAMLiquid.app
```

The app can be moved anywhere (Applications, Desktop, an external drive): the
companion runtime travels inside the bundle. Release archives contain no project
source tree and do not require a separately installed Python environment.
The regular archive downloads model files when they are first needed into the
user cache; the worker's working files (processing statistics) live in
`~/Library/Application Support/GigaAMLiquid`, never inside the bundle.

Running from a source checkout still works: when no embedded companion is found,
the client falls back to a `GigaAMTranscriber.app` beside itself (2.0–2.1.0
archives) and then to the project's Python (`GIGAAM_PYTHON`, `.venv`, `python3`)
with `python -m src.tui_worker`; `GIGAAM_PROJECT_ROOT` overrides the search.

## Offline release archive

The `GigaAMLiquid-macos-arm64-offline-*.zip` asset additionally embeds model
files under `GigaAMLiquid.app/Contents/Resources/models/hf`; no model downloads
are required and the worker runs with `HF_HUB_OFFLINE=1`.

The release app is ad-hoc signed, not notarized. On first launch macOS may ask
for confirmation in Privacy & Security.

## Live capture and LLM

The Live page captures the microphone (AVAudioEngine) and, optionally, system
audio (ScreenCaptureKit) inside GigaAMLiquid itself and streams 16 kHz PCM to
the companion worker, which runs the same live pipeline as the PyQt client:
streaming recognition, optional diarization, exports and assistant questions.
macOS asks for Microphone access on the first recording and for Screen & System
Audio Recording when system audio is enabled; the companion never requests
permissions. Sessions are written under the folder chosen on the Live page
(`~/Documents/GigaAM/live` by default) as 16 kHz recordings plus the selected
exports.

The Processing log opens at the beginning of the output, with the first line
fully visible; scroll within the log to inspect later messages.

The LLM page and Settings → LLM use the project's providers (an OpenAI-compatible
or Anthropic API, Claude Code, Codex, OpenCode, Pi, oh-my-pi or any other CLI). The API key
is stored in the Keychain.
