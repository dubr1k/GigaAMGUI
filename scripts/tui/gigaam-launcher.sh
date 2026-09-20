#!/usr/bin/env bash
# Entry point behind ~/.local/bin/gigaam. Lives in the repository so that
# `gigaam --update` refreshes it together with the TUI.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: gigaam [--data-dir PATH]        launch the terminal UI
       gigaam transcribe FILE... [options]
                                       transcribe files without the UI (scripts, agents)
       gigaam llm FILE... --mode summary [--mode tasks|terms|custom] [options]
                                       summarise transcripts without the UI
       gigaam --help                   full option list of transcribe / llm
       gigaam --update [--ref REF]     update the TUI, worker environment and PATH
       gigaam --install-skill          (re)install the agent skill into ~/.claude, ~/.codex, ~/.agents
       gigaam --version                show the installed revision
EOF
}

# --help must work even without an installation (no GIGAAM_TUI_PREFIX), so it
# is handled before the prefix check below; with an installation the binary
# appends the transcribe / llm option list.
case "${1:-}" in
  --help|-h)
    usage
    binary="${GIGAAM_TUI_PREFIX:-}/repo/tui/target/release/gigaam-tui"
    if [[ -n "${GIGAAM_TUI_PREFIX:-}" && -x "$binary" ]]; then echo; "$binary" --help; fi
    exit 0 ;;
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
  --install-skill)
    installed=0
    for target in "$HOME/.claude/skills" "$HOME/.codex/skills" "$HOME/.agents/skills"; do
      [[ -d "$target" ]] || continue
      mkdir -p "$target/gigaam" && cp "$REPO_DIR/skills/gigaam/SKILL.md" "$target/gigaam/SKILL.md" \
        && echo "Installed skill: $target/gigaam/SKILL.md" && installed=$((installed + 1))
    done
    [[ $installed -gt 0 ]] || echo "No agent skill directories found (~/.claude/skills, ~/.codex/skills, ~/.agents/skills)." >&2
    exit 0 ;;
esac

export GIGAAM_PROJECT_ROOT="$REPO_DIR"
export GIGAAM_PYTHON="$VENV/bin/python"
exec "$REPO_DIR/tui/target/release/gigaam-tui" "$@"
