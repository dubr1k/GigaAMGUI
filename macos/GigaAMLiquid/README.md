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
