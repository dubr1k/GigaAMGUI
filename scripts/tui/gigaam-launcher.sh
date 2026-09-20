#!/usr/bin/env bash
# Entry point behind ~/.local/bin/gigaam. Lives in the repository so that
# `gigaam --update` refreshes it together with the TUI.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: gigaam [--data-dir PATH]        launch the terminal UI
       gigaam --update [--ref REF]     update the TUI, worker environment and PATH
       gigaam --version                show the installed revision
EOF
}

# --help must work even without an installation (no GIGAAM_TUI_PREFIX), so it
# is handled before the prefix check below.
case "${1:-}" in
  --help|-h) usage; exit 0 ;;
esac

PREFIX="${GIGAAM_TUI_PREFIX:-}"
if [[ -z "$PREFIX" ]]; then
  echo "GIGAAM_TUI_PREFIX is not set; reinstall with scripts/install_tui.sh" >&2
  exit 2
fi
REPO_DIR="$PREFIX/repo"
VENV="$REPO_DIR/.venv"
RAW_BASE="${GIGAAM_REPOSITORY_RAW:-https://raw.githubusercontent.com/dubr1k/GigaAMGUI/main}"

case "${1:-}" in
  --version)
    revision="$(git -C "$REPO_DIR" describe --tags --always 2>/dev/null || echo unknown)"
    echo "gigaam-tui $revision ($REPO_DIR)"
    exit 0 ;;
  --update)
    shift
    installer="${GIGAAM_INSTALLER:-}"
    if [[ -z "$installer" ]]; then
      installer="$(mktemp "${TMPDIR:-/tmp}/install_tui.XXXXXX")"
      trap 'rm -f "$installer"' EXIT
      if ! curl -fsSL "$RAW_BASE/scripts/install_tui.sh" -o "$installer"; then
        echo "Cannot download the installer; using the local copy." >&2
        cp "$REPO_DIR/scripts/install_tui.sh" "$installer"
      fi
    fi
    # Not `exec`: a plain mktemp'd installer must be removed by the EXIT trap
    # above once it has run, so its exit status is captured and re-raised
    # instead of replacing this process (which would skip the trap).
    status=0
    bash "$installer" --prefix "$PREFIX" "$@" || status=$?
    exit "$status" ;;
esac

export GIGAAM_PROJECT_ROOT="$REPO_DIR"
export GIGAAM_PYTHON="$VENV/bin/python"
exec "$REPO_DIR/tui/target/release/gigaam-tui" "$@"
