---
date: 2026-06-13 00:00:00 PT
ver: 2.1.0
author: Ice-ninja
model: claude-opus-4-8
tags: [wsl2, session-recovery, terminal, agentic-cli, crash-recovery, zsh]
---

# crash-guard

```
  _____ _____ _____ _____     _____ _____ _____ _____ 
 |  _  |  _  |  _  |  _  |   |  _  |  _  |  _  |  _  |
 | |_| | |_| | |_| | |_| |   | |_| | |_| | |_| | |_| |
 |_____|_____|_____|_____|   |_____|_____|_____|_____|
|_____|_____|_____|_____|   |_____|_____|_____|_____|
```

Crash-guard keeps a durable recovery ledger for agentic CLI / TUI sessions and
restores them after WSL restarts, terminal crashes, or failed restore attempts.
It tracks Claude, Codex, opencode, Hermes, pi, Kilo, ante, and wrapper-launched
commands such as `rtk`/`xx`. The built-in program configs cover:
`claude`, `codex`, `opencode`, `hermes`, `pi`, `kilo`, `ante`, `rtk`.

It is not a PTY recorder. It stores enough metadata to relaunch the right
continue/resume command in the right working directory, through the same
captured non-secret proxy-routing environment.

## Repository Layout

```text
bin/crash-guard       executable Python tool
shell/crash-guard.sh  shell integration: cg_run, cgr, cgs, cgh
scripts/install.sh    install files only; does not edit shell rc files
README.md             setup and operating guide
CHANGELOG.md          project changes
LICENSE               MIT license
```

## Agent Install Guide

If you are an agent setting this up on a new machine, do this from WSL/bash or a
Linux shell:

```bash
git clone https://github.com/aaaronmiller/crash-guard ~/code/crash-guard
cd ~/code/crash-guard
bash scripts/install.sh
```

Add this shell integration block to `~/.zshrc` or `~/.bashrc`. The PATH
export MUST come BEFORE the source line, and both should be BEFORE any
tool aliases that use ``cg_run``:

```bash
# --- crash-guard session recovery: insert BEFORE your tool aliases ---
export PATH="$HOME/.local/bin:$PATH"      # MUST be before source line
source "$HOME/.config/crash-guard/crash-guard.sh"   # defines cg_run()
# Aliases using cg_run go AFTER the source line:
alias cgr='crash-guard restore'                                      # crash-guard restore tracked sessions
alias cgs='crash-guard status'                                       # crash-guard status/preview
alias cgr-archive='crash-guard restore --from-archive --terminal ghostty' # recover archived restore via Ghostty/tmux tabs
alias cgh='crash-guard history'                                      # crash-guard OS boot history browser
```

#### Session naming (optional)

You can tag a session with a human-readable name at launch. Names appear in
restore groups and the detailed plan, helping distinguish multiple sessions
in the same directory:

```bash
alias my-pi='cg_run --name "pi-on-crash-guard" rtk -- pi -c'
```

Then add tracked launcher aliases. On this machine, the active proxy aliases use
`xx`, so the tracked aliases are:

```bash
alias cc='cg_run xx cip'           # Claude init, proxy
alias ccc='cg_run xx ccf'          # Claude continue, proxy, free tier
alias hsi='cg_run xx hip'          # Hermes init, proxy
alias hsr='cg_run xx hcf'          # Hermes continue, proxy, free tier
alias psi='cg_run xx pip'          # Pi init, proxy
alias psi-c='cg_run xx pcf'        # Pi continue, proxy, free tier
alias qw='cg_run xx qip'           # Qwen init, proxy
alias qw-c='cg_run xx qcf'         # Qwen continue, proxy, free tier
alias codex-run='cg_run xx xip'    # Codex init, proxy
alias codex-res='cg_run xx xcf'    # Codex continue, proxy, free tier
alias oc='cg_run xx oip'           # OpenCode init, proxy
alias ante='cg_run xx aip'         # Ante init, proxy
```

If the machine does not use `xx`, wrap the real command directly:

```bash
alias cc='cg_run claude -- rtk claude --dangerously-skip-permissions'
alias ccc='cg_run claude -- rtk claude --continue --dangerously-skip-permissions'
alias codex-run='cg_run codex -- rtk codex --dangerously-bypass-approvals-and-sandbox'
alias codex-res='cg_run codex -- rtk codex resume'
alias oc='cg_run opencode -- rtk opencode'
alias hsi='cg_run hermes -- rtk hermes'
alias hsr='cg_run hermes -- rtk hermes --resume'
```

System-specific notes:

- Ghostty on Linux/Fedora: install `tmux`; crash-guard uses tmux windows as
  tab-equivalents because Ghostty exposes `new_tab` as a keybind, not a
  `+new-tab` CLI action.
- WezTerm: install `wezterm` or `wezterm.exe`; existing-window tab spawning is
  preserved.
- Kitty: enable remote control if you want native Kitty tabs; crash-guard falls
  back to a new Kitty OS window if remote launch fails.
- Windows Terminal from WSL: `wt.exe` and `wsl.exe` must be on PATH.

Open a new shell or run:

```bash
source ~/.zshrc
```

## Daily Use

Run tracked aliases normally. Each launcher writes a live sentinel before the
tool starts and appends to durable history on start, stop, archive, and restore.

Common commands:

```bash
crash-guard       # restore picker; Enter restores newest group
cgr               # same as crash-guard restore
cgs               # current live/stale/crashed sentinel status
cgh               # OS boot-period history browser
cgr-archive       # retry records already moved to archive
```

The restore picker shows recovery groups, then lets you use arrow keys or
numbers to select a group. It then lets you select all sessions in that group or
one individual session. Pressing Enter on the defaults restores the newest
recoverable group.

Restore groups show:
- **Session duration** (e.g. `(8h35m)`, `(51s)`) — sessions that ran < 5 seconds
  are automatically filtered out (they're test launches or missing commands, not
  worth restoring).
- **Git context** — branch name and dirty✓/dirty✗ indicator for each session's
  working directory.
- **Session name** — if set via `cg_run --name "my-label"`, shown in `[brackets]`.
- **Ephemeral filtering** — sessions that ran < 5 seconds are never shown.

Useful non-interactive forms:

```bash
crash-guard --dry-run
crash-guard restore --group 2
crash-guard restore --group 2 --item 3
crash-guard restore --boot <boot-id> --group 1
crash-guard restore --from-archive --terminal ghostty
crash-guard restore --no-spawn
crash-guard restore --include-closed   # also show cleanly closed sessions
crash-guard restore --include-closed --list-groups  # preview only
```

## History

Crash-guard keeps append-only history at:

```text
~/.local/share/crash-guard/history.jsonl
```

It also folds in live sentinels and archived sentinels, so a failed restore does
not erase the only recovery record.

`crash-guard history` first lists OS boot periods with record counts. In an
interactive terminal, choose a period with arrow keys or a number; crash-guard
then shows that period's recovery groups.

Recovery grouping:

- Clean exits are kept as individual closed-session records.
- Crashed, stale, or archived sessions that ended together are grouped.
- The newest recoverable group is group `1` for plain `crash-guard`.
- Older OS boot periods are restored with `--boot <boot-id>`.

Cleanup is explicit and dry-run by default:

```bash
crash-guard excise --older-than 365
crash-guard excise --older-than 365 --apply
```

`--apply` requires typing `Jettison the ghosts` before old history is removed.

## Terminal Backends

| backend | behavior |
|---------|----------|
| `tmux` | new tmux windows, meaning full-screen tab-like workspaces, not panes |
| `ghostty` | resolves to tmux on Linux for tab-like restore behavior |
| `wezterm` | new tabs in the current/sole WezTerm window when resolvable |
| `kitty` | new tabs through Kitty remote control, with OS-window fallback |
| `windows-terminal` | new Windows Terminal tabs through `wt.exe` + `wsl.exe` |

Force a backend:

```bash
crash-guard restore --terminal tmux
crash-guard restore --terminal ghostty
crash-guard restore --terminal wezterm
```

## Config

Default config lives at:

```text
~/.config/crash-guard/programs.json
```

It controls terminal backend selection, per-tool continue/resume commands,
session-store locations, captured environment patterns, the restore pre-hook,
and restore spawn delay.

Key config options:

- `terminal.backend` — `auto` (default), `tmux`, `wezterm`, `kitty`, `ghostty`, `windows-terminal`
- `terminal.order` — preference order for auto-detection
- `restore.pre` — shell command to run before each restore (default: `_proxy_stack_auto_start`)
- `restore.spawn_delay` — seconds between session restores (default: `0.5`, prevents OOM)
- `programs.<name>.continue` — command to continue a session in a directory
- `programs.<name>.resume_by_id` — command to resume by session ID (with `{id}` placeholder)
- `programs.<name>.store` — session store location for Tier 2 resume (`claude`, `codex`)
- `programs.<name>.use_sentinel_argv` — if true, replay recorded argv exactly (for wrappers like `rtk`)
- `env_patterns` — glob patterns for environment variables to capture per-session

## Troubleshooting

**`crash-guard: no wezterm CLI found`**
- Install WezTerm (`wezterm` or `wezterm.exe` on PATH) or configure `wezterm.cli` in config
- Or use `--terminal tmux` / `--terminal ghostty` to force a different backend

**`crash-guard: no supported terminal CLI found`**
- Ensure at least one terminal backend is installed: `tmux`, `wezterm`, `kitty`, `ghostty`, or Windows Terminal (`wt.exe`)
- Check `crash-guard status` to see current boot's tracked sessions

**Restore spawns too many sessions / system OOM**
- Reduce `restore.spawn_delay` in config (default 0.5s)
- Use `crash-guard restore --select` to pick specific sessions
- Use `crash-guard restore --dry-run` to preview without spawning

**Sessions show as `rtk` instead of actual tool**
- Ensure `rtk` program config has `use_sentinel_argv: true` (default in v2.0+)
- Run `crash-guard init` to update config with latest defaults

**`cg_run` alias not found**
- Ensure `source ~/.config/crash-guard/crash-guard.sh` is in your `~/.zshrc` or `~/.bashrc`
- Verify `~/.local/bin` is in PATH before sourcing

**History grows too large**
- Run `crash-guard excise --older-than 90 --apply` to remove old records
- Adjust retention period as needed

**Security: what crash-guard stores**
- `cwd` (working directory) is recorded in every sentinel — full filesystem paths
  appear in analytics and metrics. If your path contains sensitive information
  (e.g. `/home/me/code/client-acme-corp`), it will be visible in the history log
  and analytics output.
- `argv` (command arguments) is recorded in every sentinel. **Do not pass secrets
  as command-line arguments** (e.g. `--api-key sk-xxx`). Use environment
  variables or config files instead.
- `env` captures only proxy-routing environment variables (URLs, base endpoints,
  non-secret placeholders). Real API keys and auth tokens are explicitly dropped
  by `env_snapshot()`.
- History files are stored in plaintext under `~/.local/share/crash-guard/`. They
  are not encrypted. Protect your home directory accordingly.

**Session naming**
- Use `cg_run --name "my-label" key -- cmd` to tag sessions with a human-readable name
- Names appear in restore groups in `[brackets]` and in the detailed plan
- Helps distinguish multiple sessions running in the same directory

**Crash diagnostics**
- After a system crash mid-restore, the next login shows a one-line diagnostic:
  `restored 2/3 via ghostty (1 failed) — critically low memory: ~500MB available`
- This reads `history.jsonl` events which are fsynced to disk before each spawn
- The `restore_start` event includes a system snapshot (memory, process count),
  the full plan summary, and the terminal backend being used
- If you see memory warnings, reduce `restore.spawn_delay` or restore fewer sessions

**Diagnostic history events (advanced)**

The append-only `history.jsonl` now records detailed restore lifecycle:
```
restore_start: plan summary, backend, system memory/process snapshot
restore_attempt: per-item, logged BEFORE spawn (survives OOM kill)
restore_ok / restore_fail: per-item result
restore_done: final counts, system snapshot (compare before/after)
```
All restore events use `flush=True` + `os.fsync()` so they survive process kill.

## Production Notes

- Runtime state lives under `~/.local/share/crash-guard/`.
- User config lives under `~/.config/crash-guard/`.
- The repository has no build step and no third-party Python dependencies.
- Generated caches and local state are ignored by `.gitignore`.
- License: MIT.
