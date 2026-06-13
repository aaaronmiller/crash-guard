# ---
# date: 2026-06-13 00:00:00 PT
# ver: 2.1.0
# author: Ice-ninja
# model: Claude Opus 4.8
# tags: [wsl2, shell-integration, zsh, bash, session-recovery, terminal]
# ---
# crash-guard shell integration. Source this from ~/.zshrc or ~/.bashrc:
#     source ~/.config/crash-guard/crash-guard.sh
#
# Works in bash and zsh. Defines the cg_run launcher primitive, the cgr/cgs
# aliases, and a once-per-boot login notice when crashed sessions are detected.
# It does NOT redefine your tool aliases; compose cg_run into your launcher
# aliases in your shell rc.

# cg_run KEY [--] CMD [ARGS...]
#   Registers a live-session sentinel, runs CMD in the foreground (so signals,
#   the tty, and your TUI behave exactly as normal), then clears the sentinel
#   when CMD exits. A WSL2 crash kills the shell before the clear runs, which
#   is precisely how a crashed session is detected on the next boot.
#
#   Everything after the (optional) `--` is the real command. Because typed
#   args append to the end of an alias expansion, a bare alias works:
#       alias cc='... cg_run claude -- rtk claude --flags'
#       cc foo  ->  ... cg_run claude -- rtk claude --flags foo
cg_run() {
  local key="$1"; shift
  local name=""
  # Parse optional --name/-n before the -- separator
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --name|-n) name="$2"; shift 2 ;;
      --) shift; break ;;
      *) break ;;
    esac
  done
  local inv
  inv="$(cat /proc/sys/kernel/random/uuid 2>/dev/null)"
  [ -z "$inv" ] && inv="cg-$$-${RANDOM}-$(date +%s)"
  # Validate crash-guard is available
  if ! command -v crash-guard >/dev/null 2>&1; then
    echo "cg_run: crash-guard not found in PATH" >&2
    echo "  Ensure \"\$HOME/.local/bin\" is in PATH BEFORE sourcing crash-guard.sh" >&2
    echo "  Example in ~/.zshrc:" >&2
    echo "    export PATH=\"\$HOME/.local/bin:\$PATH\"" >&2
    echo "    source \"\$HOME/.config/crash-guard/crash-guard.sh\"" >&2
    "$@"
    return $?
  fi
  # Build start args as array to handle spaces in key/name/cwd safely
  local start_args=(start --key "$key" --inv-id "$inv" --cwd "$PWD" --shell-pid "$$")
  [ -n "$name" ] && start_args+=(--name "$name")
  if [ "${CG_QUIET:-0}" = "1" ]; then
    command crash-guard "${start_args[@]}" -- "$@" 2>/dev/null
  else
    command crash-guard "${start_args[@]}" -- "$@"
  fi
  "$@"
  local rc=$?
  if [ "${CG_QUIET:-0}" = "1" ]; then
    command crash-guard stop --inv-id "$inv" 2>/dev/null
  else
    command crash-guard stop --inv-id "$inv"
  fi
  if [ "$rc" -ne 0 ] && [ "${CG_QUIET:-0}" != "1" ]; then
    echo "[cg_run] sentinel removed (exit code $rc)" >&2
  fi
  return $rc
}

alias cgr='crash-guard restore'
alias cgr-batch='crash-guard restore --batch'
alias cgs='crash-guard status'
alias cgr-archive='crash-guard restore --from-archive --terminal ghostty'
alias cgh='crash-guard history'

# Once-per-boot crash notice on an interactive shell. The marker file is keyed
# to the current boot_id, so after the first interactive shell this boot the
# test below short-circuits with zero python cost.
if [ -n "${PS1:-}" ] || case "$-" in *i*) true;; *) false;; esac; then
  _cg_boot="$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)"
  _cg_marker="${XDG_DATA_HOME:-$HOME/.local/share}/crash-guard/.notified-${_cg_boot}"
  if [ -n "${_cg_boot}" ] && [ ! -e "${_cg_marker}" ]; then
    command crash-guard notify 2>/dev/null
  fi
  unset _cg_boot _cg_marker
fi

# ---------------------------------------------------------------------------
# Reference: the tracked launcher aliases the installer writes into ~/.zshrc.
# (Shown here for documentation; the live copies live in ~/.zshrc.)
#
#   # claude (proxy :8082, Anthropic protocol)
#   alias cc='_proxy_stack_auto_start && ANTHROPIC_BASE_URL=http://127.0.0.1:8082 ANTHROPIC_API_KEY=pass cg_run claude -- rtk claude --dangerously-skip-permissions'
#   alias ccc='_proxy_stack_auto_start && ANTHROPIC_BASE_URL=http://127.0.0.1:8082 ANTHROPIC_API_KEY=pass cg_run claude -- rtk claude --continue --dangerously-skip-permissions'
#
#   # opencode (proxy /p/opencode/v1)
#   alias oc='_proxy_stack_auto_start && OPENAI_BASE_URL=http://127.0.0.1:8082/p/opencode/v1 OPENAI_API_KEY=pass cg_run opencode -- rtk opencode'
#   alias occ='_proxy_stack_auto_start && OPENAI_BASE_URL=http://127.0.0.1:8082/p/opencode/v1 OPENAI_API_KEY=pass cg_run opencode -- rtk opencode -c'
#
#   # hermes (proxy /p/hermes/v1)
#   alias hsi='_proxy_stack_auto_start && OPENAI_BASE_URL=http://127.0.0.1:8082/p/hermes/v1 OPENAI_API_KEY=pass cg_run hermes -- rtk hermes --dangerously-skip-permissions'
#   alias hsr='_proxy_stack_auto_start && OPENAI_BASE_URL=http://127.0.0.1:8082/p/hermes/v1 OPENAI_API_KEY=pass cg_run hermes -- rtk hermes --resume --dangerously-skip-permissions'
#
#   # pi (proxy /p/pi/v1)
#   alias psi='_proxy_stack_auto_start && OPENAI_BASE_URL=http://127.0.0.1:8082/p/pi/v1 OPENAI_API_KEY=pass cg_run pi -- rtk pi --provider openai'
#   alias psi-c='_proxy_stack_auto_start && OPENAI_BASE_URL=http://127.0.0.1:8082/p/pi/v1 OPENAI_API_KEY=pass cg_run pi -- rtk pi --provider openai --continue'
#
#   # codex (proxy /p/codex/v1)
#   alias codex-run='_proxy_stack_auto_start && OPENAI_BASE_URL=http://127.0.0.1:8082/p/codex/v1 OPENAI_API_KEY=pass cg_run codex -- rtk codex --dangerously-bypass-approvals-and-sandbox'
#   alias codex-res='_proxy_stack_auto_start && OPENAI_BASE_URL=http://127.0.0.1:8082/p/codex/v1 OPENAI_API_KEY=pass cg_run codex -- rtk codex resume'
#
#   # ante (no proxy route; memory-based, no resume flag -> anc == an)
#   alias an='_proxy_stack_auto_start && cg_run ante -- ante repl'
#   alias anc='_proxy_stack_auto_start && cg_run ante -- ante repl'
# ---------------------------------------------------------------------------
