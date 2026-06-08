---
date: 2026-06-02 00:00:00 PT
ver: 2.0.0
author: Ice-ninja
model: claude-opus-4-8
tags: [wsl2, session-recovery, terminal, agentic-cli, crash-recovery, zsh]
---

# crash-guard

Restore tracked agentic CLI / TUI sessions after a WSL2 crash. `crash-guard`
records live sessions as small sentinel files, detects which ones survived from
a previous boot, and restores them in the right working directory through your
configured terminal.

Supported tools include Claude, Codex, opencode, Hermes, pi, Kilo, and ante.
Restore commands keep the configured `rtk` prefix and captured proxy-routing
environment so resumed sessions reconnect through the same proxy path.

## Install

```bash
bash install-crash-guard.sh && exec "$SHELL" -l
```

The installer copies:

- `~/.local/bin/crash-guard`
- `~/.config/crash-guard/crash-guard.sh`
- `~/.config/crash-guard/programs.json`

It also adds one source line to `~/.zshrc` or `~/.bashrc` if the integration is
not already loaded.

## Daily Use

Launchers are wrapped with `cg_run`, so normal interactive use stays the same
while crash-guard tracks active sessions.

| start | continue | tool |
|-------|----------|------|
| `cc` | `ccc` | Claude |
| `oc` | `occ` | opencode |
| `hsi` | `hsr` | Hermes |
| `psi` | `psi-c` | pi |
| `codex-run` | `codex-res` | Codex |
| custom | custom | Kilo |
| `an` | `anc` | ante |

After a crash or WSL restart:

```bash
crash-guard
cgs
cgh
cgr
cgr-archive
```

`cgs` previews currently live sentinels. `cgh` lists durable recovery groups.
`crash-guard` with no arguments behaves like `cgr`: it prints the grouped
restore history, defaults to the newest recoverable group, and opens each
selected session through the detected terminal backend.
`cgr-archive` retries sessions that were already moved to the archive by a
previous failed restore.

Useful restore flags:

```bash
crash-guard restore --dry-run
crash-guard --dry-run
crash-guard restore --select
crash-guard restore --repair
crash-guard restore --no-spawn
crash-guard restore --include-stale
crash-guard restore --from-archive
crash-guard restore --terminal ghostty
crash-guard restore --group 2
crash-guard restore --group 2 --item 3
crash-guard restore --boot <boot-id> --group 2
crash-guard history
crash-guard history --list
crash-guard excise --older-than 365
```

`--terminal` accepts `auto`, `tmux`, `wezterm`, `kitty`, `ghostty`, `wt`, or
`windows-terminal`.

## Terminal Backends

`terminal.backend` defaults to `auto`. Auto-detection prefers the multiplexer or
terminal that launched restore, then falls back through the configured order.

| backend | spawn behavior | notes |
|---------|----------------|-------|
| `tmux` | new tmux windows | A tmux window is tmux's tab equivalent: one full-screen active workspace, not a split pane. |
| `wezterm` | new tabs in the current/sole WezTerm window when resolvable, otherwise a new window | Uses `WEZTERM_PANE` or `wezterm cli list-clients` for WSL-aware mux lookup. |
| `kitty` | new tabs via Kitty remote control | Falls back to a new Kitty OS window if remote control launch fails. |
| `ghostty` | tmux windows in the current Ghostty tab | Ghostty has a `new_tab` keybind action but no `+new-tab` CLI action in current Linux builds, so crash-guard uses tmux windows instead of spawning Ghostty OS windows. |
| `windows-terminal` | new Windows Terminal tabs | Uses `wt.exe` and re-enters the current WSL distro with `wsl.exe --cd <cwd> --exec ...`. |

Force a backend when auto-detection is not what you want:

```bash
crash-guard restore --terminal tmux
crash-guard restore --terminal ghostty
crash-guard restore --terminal windows-terminal
```

If a previous restore attempt archived sentinels before the sessions were
actually usable, recover them with:

```bash
crash-guard restore --from-archive --terminal ghostty
```

This creates tmux windows, not panes. You get one restored session per tmux
window, each using the full terminal while active.

## Config

`~/.config/crash-guard/programs.json` controls terminal selection, per-tool
restore commands, session-store locations, captured environment patterns, and
the restore pre-hook.

Default terminal config:

```json
"terminal": {
  "backend": "auto",
  "order": ["tmux", "wezterm", "kitty", "windows-terminal"]
}
```

Per-terminal CLI overrides:

```json
"tmux": {"cli": ""},
"wezterm": {"cli": "", "domain": ""},
"kitty": {"cli": ""},
"ghostty": {"cli": ""},
"windows_terminal": {"cli": "", "wsl": ""}
```

`restore.pre` defaults to starting the local proxy stack when your shell defines
`_proxy_stack_auto_start`.

## Durable History

Crash-guard keeps an append-only JSONL history at
`~/.local/share/crash-guard/history.jsonl`. Live sentinel files and archived
sentinels are folded into the same view, so a failed restore does not erase the
only recovery record.

`crash-guard history` is an OS boot-period browser. It first lists recorded boot
periods with record counts, recoverable counts, closed counts, and running
counts. In an interactive terminal, type a number or use the arrow keys to pick
a boot period; crash-guard then shows the normal recovery-group list scoped to
that period.

Recovery groups inside a boot period are derived from the stored records:

- Cleanly closed sessions are listed as individual groups.
- Crashed, stale, or archived sessions that ended together are listed as one
  group.
- `crash-guard restore` with no group selected restores group `1`, the newest
  recoverable group.
- `crash-guard restore --group 2` restores all sessions in group `2`.
- `crash-guard restore --group 2 --item 3` restores only item `3` from group
  `2`.
- `crash-guard restore --boot <boot-id> --group 2` restores group `2` from an
  older OS boot period.

History is retained indefinitely by default. Cleanup is explicit:

```bash
crash-guard excise --older-than 365
crash-guard excise --older-than 365 --apply
```

Without `--apply`, `excise` only reports how many events and archive files would
be affected and how many archive bytes would be reclaimed. With `--apply`, it
requires typing `Jettison the ghosts` before removing old history.

## How It Works

- `cg_run` writes one sentinel under `~/.local/share/crash-guard/live/` before
  launching a tracked TUI and removes it after clean exit.
- `start`, `stop`, `archive`, and `restore` events are appended to durable
  history.
- Each sentinel records the current kernel `boot_id`. After a WSL restart,
  old sentinels have a different boot id and are classified as crashed.
- Restore is CWD-first: it runs the configured continue command in the recorded
  directory.
- Wrapper-launched sentinels are normalized at restore time. For example, a
  sentinel recorded as `rtk` with argv `claude ...` restores through the Claude
  config instead of being skipped as an unknown `rtk` program.
- `--from-archive` restores sentinel records that were already moved to
  `~/.local/share/crash-guard/archive/`.
- If several instances of the same tool ran in the same directory, restore can
  resolve session ids from supported tool stores and use `resume_by_id`.
- `--repair` can truncate null-padded or partial session artifacts after making
  a backup in `~/.local/share/crash-guard/archive/`.

## Tool Notes

- Resume flags verified 2026-06: Claude (`-c` / `-r <id>`), Codex
  (`resume --last` / `resume <id>`), opencode (`-c` / `-s <id>`), Hermes
  (`--continue` / `--resume <id>`), pi (`-c` / `--session <id>`), Kilo (`-c`).
- ante has no session-resume flag; restore relaunches `ante repl` in the
  recorded directory and relies on ante memory injection.
- Exact multi-instance restore is precise for Claude and best-effort for Codex.
  Other tools degrade to CWD-first continue with a visible note.

## Repository

This project is licensed under the MIT License. Runtime state and local config
live under XDG config/data directories, not in the repository.
