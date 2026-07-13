# @dubr1k/gigaam-tui

Installs and launches the GigaAM terminal UI from the main repository:

```bash
npm install -g @dubr1k/gigaam-tui
gigaam
```

On first launch it clones the repository, builds the Rust frontend, creates an isolated Python virtual environment, and installs ML dependencies. Required build tools: Git, Rust, Python 3.10+, FFmpeg, and a C/C++ build toolchain.

Set `GIGAAM_HOME` to choose another local installation directory.
