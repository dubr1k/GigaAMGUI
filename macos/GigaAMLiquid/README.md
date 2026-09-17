# GigaAMLiquid for macOS

GigaAMLiquid is the native Swift/AppKit client for GigaAM Transcriber. It uses
the Python worker from this repository for model inference, media downloads and
export.

## Requirements

- macOS 13 or newer on Apple Silicon.

## First launch from a release archive

Extract the archive without moving its contents apart, then open:

```bash
open GigaAMLiquid.app
```

Keep `GigaAMLiquid.app` beside the bundled `GigaAMTranscriber.app`. The latter
is the frozen companion runtime; release archives contain no project source
tree and do not require a separately installed Python environment.
The regular archive downloads model files when they are first needed.

## Offline release archive

The `GigaAMLiquid-macos-arm64-offline-*.zip` asset additionally contains model
files under `models/hf`. Keep both applications and the models directory in the
extracted release folder; no model downloads are required.

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

The LLM page and Settings → LLM use the project's providers (an OpenAI-compatible
or Anthropic API, Claude Code, Codex, OpenCode, Pi or any other CLI). The API key
is stored in the Keychain.
