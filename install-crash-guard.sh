#!/usr/bin/env bash
# ---
# date: 2026-06-02 00:00:00 PT
# ver: 2.0.0
# author: Ice-ninja
# model: Claude Opus 4.8
# tags: [wsl2, installer, session-recovery, wezterm, agentic-cli]
# ---
# Installer for crash-guard. Run from the repo (it copies the sibling files):
#     bash install-crash-guard.sh
#
# Installs:
#   ~/.local/bin/crash-guard            (the python tool)
#   ~/.config/crash-guard/crash-guard.sh (shell integration)
#   ~/.config/crash-guard/programs.json  (default config via `crash-guard init`)
# and wires an idempotent source-line into ~/.zshrc (or ~/.bashrc) so it loads
# on every WSL shell start. It does NOT modify your tool aliases.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
CFG_DIR="$HOME/.config/crash-guard"
mkdir -p "$BIN_DIR" "$CFG_DIR"

# --- copy the tool + shell integration from alongside this installer ---------
if [ ! -f "$SRC_DIR/crash-guard" ] || [ ! -f "$SRC_DIR/crash-guard.sh" ]; then
  echo "error: crash-guard and crash-guard.sh must sit next to this installer." >&2
  echo "       (found in: $SRC_DIR)" >&2
  exit 1
fi
install -m 0755 "$SRC_DIR/crash-guard" "$BIN_DIR/crash-guard"
install -m 0644 "$SRC_DIR/crash-guard.sh" "$CFG_DIR/crash-guard.sh"

# --- write default config (idempotent; never clobbers an existing one) -------
PATH="$BIN_DIR:$PATH" python3 "$BIN_DIR/crash-guard" init >/dev/null 2>&1 || true

# --- wire the rc file idempotently -------------------------------------------
RC="$HOME/.zshrc"
[ -f "$HOME/.zshrc" ] || RC="$HOME/.bashrc"
touch "$RC"
if ! grep -q 'crash-guard/crash-guard.sh' "$RC" 2>/dev/null; then
  cp "$RC" "$RC.crash-guard.bak-$(date +%Y%m%d-%H%M%S)"
  printf '\n# crash-guard session recovery\nexport PATH="$HOME/.local/bin:$PATH"\nsource "$HOME/.config/crash-guard/crash-guard.sh"\n' >> "$RC"
  echo "wired crash-guard into $RC (backup made alongside)"
else
  echo "crash-guard already wired into $RC"
fi

echo ""
echo "crash-guard installed."
echo "  binary : $BIN_DIR/crash-guard"
echo "  shell  : $CFG_DIR/crash-guard.sh"
echo "  config : $CFG_DIR/programs.json"
echo ""
echo "Open a new shell (or: source \"$RC\"), then wrap launchers with cg_run."
echo "Verified resume flags: claude, codex, opencode, hermes, pi. ante = relaunch (memory-based)."
echo "After a crash, from any WezTerm tab run:  cgr    (preview: cgs)"
