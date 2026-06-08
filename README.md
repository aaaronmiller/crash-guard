---
date: 2026-06-02 00:00:00 PT
ver: 2.0.0
author: Ice-ninja
model: claude-opus-4-8
tags: [wsl2, session-recovery, terminal, agentic-cli, crash-recovery, zsh]
---

# crash-guard

```
   ____               __       ______                     __
  / __/______ ____  / /  ___ / ___/_ _____ _________ ___/ /
 _\ \/ __/ _ `/ _ \/ _ \/ -_) (_ / // / _ `/ __/ _ `/ _  /
/___/\__/\_,_/_//_/_//_/\__/\___/\_,_/\_,_/_/  \_,_/\_,_/
```

Crash-guard keeps a durable recovery ledger for agentic CLI / TUI sessions and
restores them after WSL restarts, terminal crashes, or failed restore attempts.
It tracks Claude, Codex, opencode, Hermes, pi, Kilo, ante, and wrapper-launched
commands such as `rtk`/`xx`.

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

Add this shell integration block to `~/.zshrc` or `~/.bashrc`:

```bash
# crash-guard session recovery
export PATH="$HOME/.local/bin:$PATH"
source "$HOME/.config/crash-guard/crash-guard.sh"
alias cgr='crash-guard restore'                                      # crash-guard restore tracked sessions
alias cgs='crash-guard status'                                       # crash-guard status/preview
alias cgr-archive='crash-guard restore --from-archive --terminal ghostty' # recover archived restore via Ghostty/tmux tabs
alias cgh='crash-guard history'                                      # crash-guard OS boot history browser
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

Useful non-interactive forms:

```bash
crash-guard --dry-run
crash-guard restore --group 2
crash-guard restore --group 2 --item 3
crash-guard restore --boot <boot-id> --group 1
crash-guard restore --from-archive --terminal ghostty
crash-guard restore --no-spawn
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
session-store locations, captured environment patterns, and the restore pre-hook.

## Production Notes

- Runtime state lives under `~/.local/share/crash-guard/`.
- User config lives under `~/.config/crash-guard/`.
- The repository has no build step and no third-party Python dependencies.
- Generated caches and local state are ignored by `.gitignore`.
- License: MIT.
