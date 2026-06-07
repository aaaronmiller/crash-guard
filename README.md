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
cgs
cgr
```

`cgs` previews tracked sessions. `cgr` prints the restore plan, asks once, and
opens each selected session through the detected terminal backend.

Useful restore flags:

```bash
crash-guard restore --dry-run
crash-guard restore --select
crash-guard restore --repair
crash-guard restore --no-spawn
crash-guard restore --include-stale
crash-guard restore --terminal ghostty
```

`--terminal` accepts `auto`, `tmux`, `wezterm`, `kitty`, `ghostty`, `wt`, or
`windows-terminal`.

## Terminal Backends

`terminal.backend` defaults to `auto`. Auto-detection prefers the multiplexer or
terminal that launched restore, then falls back through the configured order.

| backend | spawn behavior | notes |
|---------|----------------|-------|
| `tmux` | new tmux windows | Requires running `restore` inside tmux. Works inside common terminal emulators. |
| `wezterm` | new tabs in the current/sole WezTerm window when resolvable, otherwise a new window | Uses `WEZTERM_PANE` or `wezterm cli list-clients` for WSL-aware mux lookup. |
| `kitty` | new tabs via Kitty remote control | Falls back to a new Kitty OS window if remote control launch fails. |
| `ghostty` | new Ghostty windows | Ghostty Linux exposes `+new-window`; crash-guard does not assume a stable current-window tab CLI. |
| `windows-terminal` | new Windows Terminal tabs | Uses `wt.exe` and re-enters the current WSL distro with `wsl.exe --cd <cwd> --exec ...`. |

Force a backend when auto-detection is not what you want:

```bash
crash-guard restore --terminal tmux
crash-guard restore --terminal ghostty
crash-guard restore --terminal windows-terminal
```

## Config

`~/.config/crash-guard/programs.json` controls terminal selection, per-tool
restore commands, session-store locations, captured environment patterns, and
the restore pre-hook.

Default terminal config:

```json
"terminal": {
  "backend": "auto",
  "order": ["tmux", "wezterm", "kitty", "ghostty", "windows-terminal"]
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

## How It Works

- `cg_run` writes one sentinel under `~/.local/share/crash-guard/live/` before
  launching a tracked TUI and removes it after clean exit.
- Each sentinel records the current kernel `boot_id`. After a WSL restart,
  old sentinels have a different boot id and are classified as crashed.
- Restore is CWD-first: it runs the configured continue command in the recorded
  directory.
- Wrapper-launched sentinels are normalized at restore time. For example, a
  sentinel recorded as `rtk` with argv `claude ...` restores through the Claude
  config instead of being skipped as an unknown `rtk` program.
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
