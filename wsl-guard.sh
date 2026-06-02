# ---
# date: 2026-06-01 00:00:00 PT
# ver: 1.0.0
# author: Ice-ninja
# model: Claude Opus 4.8
# tags: [wsl2, shell-integration, zsh, bash, session-recovery, wezterm]
# ---
# wsl-guard shell integration. Source this from ~/.zshrc or ~/.bashrc:
#     source ~/.config/wsl-guard/wsl-guard.sh
#
# Works in both bash and zsh. Defines the wg_run launcher primitive and two
# convenience aliases. It deliberately does NOT redefine your existing tool
# aliases (cc, qw, rtk, ...). You compose wg_run into them yourself; see the
# commented examples at the bottom.

# wg_run KEY [--] CMD [ARGS...]
#   Registers a live-session sentinel, runs CMD in the foreground (so signals,
#   the tty, and your TUI behave exactly as normal), then clears the sentinel
#   when CMD exits. A WSL2 crash kills the shell before the clear runs, which
#   is precisely how a crashed session is detected on the next boot.
wg_run() {
  local key="$1"; shift
  if [ "${1:-}" = "--" ]; then shift; fi
  local inv
  inv="$(cat /proc/sys/kernel/random/uuid 2>/dev/null)"
  [ -z "$inv" ] && inv="wg-$$-${RANDOM}-$(date +%s)"
  command wsl-guard start --key "$key" --inv-id "$inv" \
      --cwd "$PWD" --shell-pid "$$" -- "$@" 2>/dev/null
  "$@"
  local rc=$?
  command wsl-guard stop --inv-id "$inv" 2>/dev/null
  return $rc
}

alias wgr='wsl-guard restore'
alias wgs='wsl-guard status'

# ---------------------------------------------------------------------------
# Composition examples. Copy, rename, and adapt to your real proxy stack.
# Env vars set on the same line are exported to the tool and captured in the
# sentinel, so restore reconnects through the same proxy/gateway after a crash.
#
# To keep RTK compression on restored sessions too, put your prefix in the
# config resume command, e.g. set claude.resume_by_id to
#   ["rtk","claude","-r","{id}"]   in ~/.config/wsl-guard/programs.json
#
# Claude Code through your Ultimate Proxy on :8082, tracked:
#   cc() { ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-http://localhost:8082}" \
#          ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-pass}" \
#          wg_run claude -- rtk claude "$@"; }
#
# Codex, tracked:
#   cx() { wg_run codex -- codex "$@"; }
#
# OpenCode, tracked:
#   oc() { wg_run opencode -- opencode "$@"; }
#
# Hermes, Pi, Antigravity, Kilo, Ante: wrap the same way once you have set
# their real resume flags in programs.json:
#   hm() { wg_run hermes -- hermes "$@"; }
#   pi() { wg_run pi -- pi "$@"; }
# ---------------------------------------------------------------------------
