#!/usr/bin/env bash
# Install GigaAM TUI from the main repository (Linux and macOS).
set -euo pipefail

REPOSITORY="${GIGAAM_REPOSITORY:-https://github.com/dubr1k/GigaAMGUI.git}"
PREFIX="${GIGAAM_HOME:-$HOME/.local/share/gigaam-tui}"
BIN_DIR="${HOME}/.local/bin"

usage() {
  cat <<'EOF'
Usage: install_tui.sh [--prefix PATH] [--ref GIT_REF] [--model MODEL]

Installs the Rust TUI, an isolated Python worker environment, and ~/.local/bin/gigaam.
Required tools: git, cargo, Python 3.10–3.12, ffmpeg, and a C/C++ build toolchain.
EOF
}

REF="main"
MODEL="${GIGAAM_MODEL:-v3_e2e_rnnt}"
MODEL_EXPLICIT=false
while (($#)); do
  case "$1" in
    --prefix) PREFIX="$2"; shift ;;
    --ref) REF="$2"; shift ;;
    --model) MODEL="$2"; MODEL_EXPLICIT=true; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [[ -t 0 && "$MODEL_EXPLICIT" == false && -z "${GIGAAM_MODEL:-}" ]]; then
  echo "Choose the model to download on first transcription:"
  select choice in "GigaAM v3 e2e RNNT (current)" "Multilingual CTC (220M)" "Multilingual Large CTC (600M)"; do
    case "$REPLY" in
      1) MODEL="v3_e2e_rnnt"; break ;;
      2) MODEL="multilingual_ctc"; break ;;
      3) MODEL="multilingual_large_ctc"; break ;;
      *) echo "Enter 1, 2, or 3." ;;
    esac
  done
fi
case "$MODEL" in
  v3_e2e_rnnt|multilingual_ctc|multilingual_large_ctc) ;;
  *) echo "Unknown model: $MODEL" >&2; exit 2 ;;
esac

for command in git cargo ffmpeg; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "Missing required command: $command" >&2
    exit 1
  }
done

PYTHON="${GIGAAM_PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  for candidate in python3.12 python3.11 python3.10; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON="$(command -v "$candidate")"
      break
    fi
  done
fi
if [[ -z "$PYTHON" ]] || ! "$PYTHON" -c 'import sys; raise SystemExit(not ((3, 10) <= sys.version_info[:2] <= (3, 12)))'; then
  echo "GigaAM TUI requires Python 3.10–3.12. Set GIGAAM_PYTHON to a compatible interpreter." >&2
  exit 1
fi

REPO_DIR="$PREFIX/repo"
VENV="$REPO_DIR/.venv"
# PyTorch/CUDA wheels need several GB while unpacking. /tmp is often a small
# tmpfs, so keep pip's temporary files beside the installation on the disk.
export TMPDIR="$PREFIX/tmp"
mkdir -p "$PREFIX" "$BIN_DIR" "$TMPDIR"
if [[ -d "$REPO_DIR/.git" ]]; then
  git -C "$REPO_DIR" fetch --depth 1 origin "$REF"
  git -C "$REPO_DIR" checkout --force FETCH_HEAD
else
  git clone --depth 1 --branch "$REF" "$REPOSITORY" "$REPO_DIR"
fi

cargo build --release --manifest-path "$REPO_DIR/tui/Cargo.toml"
rm -rf "$VENV"
"$PYTHON" -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip 'setuptools<81' wheel
"$VENV/bin/python" -m pip install -r "$REPO_DIR/requirements-tui.txt"
"$VENV/bin/python" -m pip install --no-build-isolation \
  -e 'git+https://github.com/salute-developers/GigaAM.git@559d88d6b72541412743929f633a6ae7c9950b85#egg=gigaam'
"$VENV/bin/python" -c 'import dotenv, gigaam'

CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
if [[ "$(uname -s)" == "Darwin" ]]; then CONFIG_HOME="$HOME/Library/Application Support"; fi
SETTINGS_DIR="$CONFIG_HOME/GigaAMTranscriber"
mkdir -p "$SETTINGS_DIR"
"$VENV/bin/python" - "$SETTINGS_DIR/tui_settings.json" "$MODEL" <<'PY'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
try:
    settings = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    settings = {}
settings["model"] = sys.argv[2]
path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

echo "Selected model: $MODEL (weights download on first transcription)."

cat > "$BIN_DIR/gigaam" <<EOF
#!/usr/bin/env bash
export GIGAAM_PROJECT_ROOT="$REPO_DIR"
export GIGAAM_PYTHON="$VENV/bin/python"
exec "$REPO_DIR/tui/target/release/gigaam-tui" "\$@"
EOF
chmod +x "$BIN_DIR/gigaam"

echo "Installed GigaAM TUI. Run: gigaam"
echo "Ensure $BIN_DIR is in your PATH."
