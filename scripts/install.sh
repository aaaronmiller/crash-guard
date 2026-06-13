#!/usr/bin/env bash
# ---
# date: 2026-06-13 00:00:00 PT
# ver: 2.1.0
# author: Ice-ninja
# model: Claude Opus 4.8
# tags: [wsl2, installer, session-recovery, terminal, agentic-cli]
# ---
# Installer for crash-guard. Run from the repo:
#     bash scripts/install.sh
#
# Installs:
#   ~/.local/bin/crash-guard            (the python tool)
#   ~/.config/crash-guard/crash-guard.sh (shell integration)
#   ~/.config/crash-guard/programs.json  (default config via `crash-guard init`)
# It does not edit shell rc files. The README contains the shell block to add.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
BIN_DIR="$HOME/.local/bin"
CFG_DIR="$HOME/.config/crash-guard"
mkdir -p "$BIN_DIR" "$CFG_DIR"

# --- verify source files ----------------------------------------------------
if [ ! -f "$SRC_DIR/bin/crash-guard" ] || [ ! -f "$SRC_DIR/shell/crash-guard.sh" ]; then
  echo "error: expected bin/crash-guard and shell/crash-guard.sh under $SRC_DIR." >&2
  echo "       (found in: $SRC_DIR)" >&2
  exit 1
fi
if [ ! -x "$SRC_DIR/bin/crash-guard" ]; then
  echo "error: $SRC_DIR/bin/crash-guard is not executable" >&2
  exit 1
fi

# --- verify python3 ---------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 not found in PATH" >&2
  exit 1
fi

# --- version check: don't downgrade silently ---------------------------------
INSTALLED_VER=""
if [ -f "$BIN_DIR/crash-guard" ]; then
  INSTALLED_VER="$(head -5 "$BIN_DIR/crash-guard" | grep '^# ver:' | sed 's/# ver: //')"
fi
SRC_VER="$(head -5 "$SRC_DIR/bin/crash-guard" | grep '^# ver:' | sed 's/# ver: //')"
if [ -n "$INSTALLED_VER" ] && [ "$INSTALLED_VER" != "$SRC_VER" ]; then
  echo "info: upgrading crash-guard $INSTALLED_VER → $SRC_VER"
fi
if [ -n "$INSTALLED_VER" ] && [ "$(printf '%s\n' "$INSTALLED_VER" "$SRC_VER" | sort -V | tail -1)" != "$SRC_VER" ]; then
  echo "warning: installed version $INSTALLED_VER is newer than source $SRC_VER"
  echo "  (the install dir may be behind the installed binary)"
fi

# --- copy the tool + shell integration --------------------------------------
install -m 0755 "$SRC_DIR/bin/crash-guard" "$BIN_DIR/crash-guard"
install -m 0644 "$SRC_DIR/shell/crash-guard.sh" "$CFG_DIR/crash-guard.sh"

# --- write repo path marker for self-update ----------------------------------
echo "$SRC_DIR" > "$CFG_DIR/.crash-guard-repo"

# --- write default config (idempotent; never clobbers an existing one) -------
echo "Initializing config..."
if ! PATH="$BIN_DIR:$PATH" python3 "$BIN_DIR/crash-guard" init; then
  echo "warning: config initialization had issues (continuing)" >&2
fi

# --- verify installation ----------------------------------------------------
if ! "$BIN_DIR/crash-guard" --help >/dev/null 2>&1; then
  echo "error: installed crash-guard binary failed to run" >&2
  exit 1
fi

echo ""
echo "crash-guard installed successfully."
echo "  binary : $BIN_DIR/crash-guard"
echo "  shell  : $CFG_DIR/crash-guard.sh"
echo "  config : $CFG_DIR/programs.json"
echo ""
echo "Next steps:"
echo "  1. Add the README shell integration block to your shell rc (~/.zshrc or ~/.bashrc)"
echo "  2. Ensure \$HOME/.local/bin is in your PATH"
echo "  3. Open a new shell or run: source ~/.zshrc"
echo ""
echo "Verified resume flags: claude, codex, opencode, hermes, pi. ante = relaunch (memory-based)."
echo "After a crash, run:  crash-guard    (preview periods: cgh, live status: cgs)"
