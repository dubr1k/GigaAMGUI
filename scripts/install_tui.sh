#!/usr/bin/env bash
# Install GigaAM TUI from the main repository (Linux and macOS).
set -euo pipefail

REPOSITORY="${GIGAAM_REPOSITORY:-https://github.com/dubr1k/GigaAMGUI.git}"
PREFIX="${GIGAAM_HOME:-$HOME/.local/share/gigaam-tui}"
BIN_DIR="${HOME}/.local/bin"

usage() {
  cat <<'EOF'
Usage: install_tui.sh [--prefix PATH] [--ref GIT_REF] [--model MODEL] [--no-path] [--no-skill] [--fresh]

Installs the Rust TUI, an isolated Python worker environment, and ~/.local/bin/gigaam.
Required tools: git, cargo, Python 3.10–3.12, ffmpeg, and a C/C++ build toolchain.

  --no-path  do not touch shell rc files to add ~/.local/bin to PATH
  --no-skill do not copy the agent skill into ~/.claude/skills, ~/.codex/skills, ~/.agents/skills
  --no-mlx   skip requirements-macos-mlx.txt on Apple Silicon (the TUI's "mlx" backend will be unavailable)
  --fresh    wipe the repo checkout and rebuild the venv from scratch
EOF
}

REF="main"
MODEL="${GIGAAM_MODEL:-v3_e2e_rnnt}"
MODEL_EXPLICIT=false
ADD_PATH=true
INSTALL_SKILL=true
INSTALL_MLX=true
FRESH=false
while (($#)); do
  case "$1" in
    --prefix) PREFIX="$2"; shift ;;
    --ref) REF="$2"; shift ;;
    --model) MODEL="$2"; MODEL_EXPLICIT=true; shift ;;
    --no-path) ADD_PATH=false ;;
    --no-skill) INSTALL_SKILL=false ;;
    --no-mlx) INSTALL_MLX=false ;;
    --fresh) FRESH=true ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

# Должно совпадать с src/config.py:user_config_dir() и tui/src/main.rs:settings_path().
if [[ -n "${GIGAAM_CONFIG_DIR:-}" ]]; then
  SETTINGS_DIR="$GIGAAM_CONFIG_DIR"
elif [[ "$(uname -s)" == "Darwin" ]]; then
  SETTINGS_DIR="$HOME/Library/Application Support/GigaAMTranscriber"
else
  SETTINGS_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/GigaAMTranscriber"
fi
SETTINGS_FILE="$SETTINGS_DIR/tui_settings.json"

# Обновление (curl | bash, без TTY) не должно сбрасывать выбранную модель.
if [[ "$MODEL_EXPLICIT" == false && -z "${GIGAAM_MODEL:-}" && -f "$SETTINGS_FILE" ]]; then
  saved_model="$(sed -n 's/.*"model"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$SETTINGS_FILE" | head -n1)"
  case "$saved_model" in
    v3_e2e_rnnt|multilingual_ctc|multilingual_large_ctc) MODEL="$saved_model"; MODEL_EXPLICIT=true ;;
  esac
fi
if [[ "${GIGAAM_INSTALL_STAGE:-}" == "print-model" ]]; then echo "$MODEL"; exit 0; fi

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

ensure_path_in_shell() {
  case ":$PATH:" in *":$HOME/.local/bin:"*) return 0 ;; esac
  local shell_name; shell_name="$(basename "${SHELL:-}")"
  case "$shell_name" in
    fish)
      local conf="$HOME/.config/fish/conf.d/gigaam.fish"
      mkdir -p "$(dirname "$conf")"
      if [[ ! -f "$conf" ]] || ! grep -q fish_add_path "$conf"; then
        printf '# gigaam-tui: make ~/.local/bin/gigaam available\nfish_add_path --global --move "$HOME/.local/bin"\n' > "$conf"
        echo "Added ~/.local/bin to PATH via $conf"
      fi ;;
    zsh|bash)
      local rc="$HOME/.zshrc"; [[ "$shell_name" == bash ]] && rc="$HOME/.bashrc"
      if [[ ! -f "$rc" ]] || ! grep -q '# gigaam-tui' "$rc"; then
        printf '\n# gigaam-tui: make ~/.local/bin/gigaam available\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"
        echo "Added ~/.local/bin to PATH via $rc"
      fi ;;
    *)
      echo "Add $HOME/.local/bin to your PATH to run gigaam." ;;
  esac
  echo "Open a new terminal (or re-source your shell config) to pick it up."
}
if [[ "${GIGAAM_INSTALL_STAGE:-}" == "path-only" ]]; then ensure_path_in_shell; exit 0; fi

   install_prerequisites() {
     local os
     os="$(uname -s)"

     if [[ "$os" == "Darwin" ]]; then
       # Apple Command Line Tools нужны для сборки Rust-зависимостей.
       if ! xcode-select -p >/dev/null 2>&1; then
         xcode-select --install || true
         echo "Install Apple Command Line Tools in the dialog, then run this script again." >&2
         exit 1
       fi

       if ! command -v brew >/dev/null 2>&1; then
         NONINTERACTIVE=1 /bin/bash -c \
           "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

         if [[ -x /opt/homebrew/bin/brew ]]; then
           eval "$(/opt/homebrew/bin/brew shellenv)"
         elif [[ -x /usr/local/bin/brew ]]; then
           eval "$(/usr/local/bin/brew shellenv)"
         fi
       fi

       brew install git ffmpeg python@3.12
       export GIGAAM_PYTHON="${GIGAAM_PYTHON:-$(brew --prefix python@3.12)/bin/python3.12}"

     elif [[ "$os" == "Linux" ]]; then
       local sudo_cmd=()
       if [[ $EUID -ne 0 ]]; then
         command -v sudo >/dev/null 2>&1 || {
           echo "sudo is required to install dependencies." >&2
           exit 1
         }
         sudo_cmd=(sudo)
       fi

       if command -v apt-get >/dev/null 2>&1; then
         "${sudo_cmd[@]}" apt-get update
         "${sudo_cmd[@]}" apt-get install -y \
           curl git ffmpeg build-essential python3 python3-venv
       elif command -v dnf >/dev/null 2>&1; then
         "${sudo_cmd[@]}" dnf install -y \
           curl git ffmpeg gcc gcc-c++ make python3
       elif command -v pacman >/dev/null 2>&1; then
         "${sudo_cmd[@]}" pacman -Sy --noconfirm \
           curl git ffmpeg base-devel python
       else
         echo "Unsupported Linux package manager. Install git, ffmpeg, Python and a C/C++
 toolchain." >&2
         exit 1
       fi
     else
       echo "Unsupported operating system: $os" >&2
       exit 1
     fi

     # rustup устанавливает cargo в ~/.cargo/bin
     if ! command -v cargo >/dev/null 2>&1 && [[ ! -x "$HOME/.cargo/bin/cargo" ]]; then
       curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
         | sh -s -- -y --profile minimal
     fi
     export PATH="$HOME/.cargo/bin:$PATH"
   }

   install_prerequisites

   for command in git cargo ffmpeg; do
     command -v "$command" >/dev/null 2>&1 || {
       echo "Missing required command after installation: $command" >&2
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
  # User preferences live in ~/.config/GigaAMTranscriber and are untouched.
  if [[ "$FRESH" == true ]]; then
    git -C "$REPO_DIR" clean -ffdx          # включая .venv и tui/target
  else
    # Держим venv и cargo-кэш: обновление не должно заново качать PyTorch.
    git -C "$REPO_DIR" clean -ffdx -e .venv -e tui/target
  fi
else
  git clone --depth 1 --branch "$REF" "$REPOSITORY" "$REPO_DIR"
fi

cargo build --release --manifest-path "$REPO_DIR/tui/Cargo.toml"
if [[ "$FRESH" == true || ! -x "$VENV/bin/python" ]]; then
  rm -rf "$VENV"
  "$PYTHON" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --upgrade pip 'setuptools<81' wheel
"$VENV/bin/python" -m pip install -r "$REPO_DIR/requirements-tui.txt"
# The TUI offers /backend mlx on macOS and the desktop app's saved backend syncs
# into it, so Apple Silicon installs need the MLX runtime in the worker venv too.
if [[ "$INSTALL_MLX" == true && "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
  "$VENV/bin/python" -m pip install -r "$REPO_DIR/requirements-macos-mlx.txt"
fi
"$VENV/bin/python" -m pip install --no-build-isolation \
  -e 'git+https://github.com/salute-developers/GigaAM.git@559d88d6b72541412743929f633a6ae7c9950b85#egg=gigaam'
if [[ "$INSTALL_MLX" == true && "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
  "$VENV/bin/python" -c 'import dotenv, gigaam, mlx, gigaam_mlx'
else
  "$VENV/bin/python" -c 'import dotenv, gigaam'
fi

mkdir -p "$SETTINGS_DIR"
"$VENV/bin/python" - "$SETTINGS_FILE" "$MODEL" <<'PY'
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
export GIGAAM_TUI_PREFIX="$PREFIX"
exec bash "$REPO_DIR/scripts/tui/gigaam-launcher.sh" "\$@"
EOF
chmod +x "$BIN_DIR/gigaam"

echo "Installed GigaAM TUI. Run: gigaam"
# Agents (Claude Code, Codex, ...) learn `gigaam transcribe` / `gigaam llm` from
# the skill file; it only goes into skill directories that already exist.
if [[ "$INSTALL_SKILL" == true ]]; then
  GIGAAM_TUI_PREFIX="$PREFIX" bash "$REPO_DIR/scripts/tui/gigaam-launcher.sh" --install-skill
fi
if [[ "$ADD_PATH" == true ]]; then ensure_path_in_shell; fi
