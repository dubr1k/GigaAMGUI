#!/usr/bin/env bash
# Install GigaAM TUI from the main repository (Linux and macOS).
set -euo pipefail

REPOSITORY="${GIGAAM_REPOSITORY:-https://github.com/dubr1k/GigaAMGUI.git}"
PREFIX="${GIGAAM_HOME:-$HOME/.local/share/gigaam-tui}"
BIN_DIR="${HOME}/.local/bin"

usage() {
  cat <<'EOF'
Usage: install_tui.sh [--prefix PATH] [--ref GIT_REF]

Installs the Rust TUI, an isolated Python worker environment, and ~/.local/bin/gigaam.
Required tools: git, cargo, Python 3.10–3.12, ffmpeg, and a C/C++ build toolchain.
EOF
}

REF="main"
while (($#)); do
  case "$1" in
    --prefix) PREFIX="$2"; shift ;;
    --ref) REF="$2"; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

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
mkdir -p "$PREFIX" "$BIN_DIR"
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
  -e 'git+https://github.com/salute-developers/GigaAM.git@0a3f1036d93287d5ef226911ec795bde8ef05d57#egg=gigaam'
"$VENV/bin/python" -c 'import dotenv, gigaam'

cat > "$BIN_DIR/gigaam" <<EOF
#!/usr/bin/env bash
export GIGAAM_PROJECT_ROOT="$REPO_DIR"
export GIGAAM_PYTHON="$VENV/bin/python"
exec "$REPO_DIR/tui/target/release/gigaam-tui" "\$@"
EOF
chmod +x "$BIN_DIR/gigaam"

echo "Installed GigaAM TUI. Run: gigaam"
echo "Ensure $BIN_DIR is in your PATH."
