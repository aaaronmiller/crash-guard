---
date: 2026-06-02 00:00:00 PT
ver: 2.0.0
author: Ice-ninja
model: claude-opus-4-8
tags: [wsl2, session-recovery, wezterm, agentic-cli, crash-recovery, zsh]
---

# crash-guard

Restore every open agentic CLI / TUI session (claude, codex, opencode, hermes,
pi, ante, ...) after a WSL2 crash. One command rebuilds them as **tabs in your
current WezTerm window**, each `cd`'d back to its directory and reconnected
through your proxy.

## How it works

- **Filesystem is the state store.** No daemon, no socket, no manifest. While a
  tool runs, a one-file sentinel exists under `~/.local/share/crash-guard/live/`.
  `cg_run` writes it before launch and deletes it on clean exit. A crash kills
  the shell before the delete, leaving the sentinel behind.
- **Boot-epoch crash detection.** Each sentinel records the kernel `boot_id`.
  After a reboot the current `boot_id` differs, so leftover sentinels are
  unambiguously *crashed* (not merely a closed tab). PID checks only happen
  within one boot, so recycled PIDs never cause a false "still running".
- **CWD-first restore.** Tier 1 = `cd <dir> && <tool> --continue`. Tier 2
  (resume by session id, resolved by mtime from the tool's own store) only
  fires when several instances of one tool shared a directory.
- **Artifact verify/repair.** A crash can leave a session file null-padded or
  half-written. Restore verifies the target and (`--repair`) strips the bad
  tail, backing up the original first, before resuming.
- **Existing-window spawn.** Run `cgr` from any WezTerm tab; the window is found
  via `WEZTERM_PANE` (native Linux/macOS) or, under WSL where that var is not
  exported, via the mux's focused pane (`wezterm cli list-clients`), falling
  back to the sole window. Every restored session opens as a new tab in it.

## Install

```bash
bash install-crash-guard.sh && exec "$SHELL" -l
```

Installs `~/.local/bin/crash-guard`, `~/.config/crash-guard/crash-guard.sh`,
`~/.config/crash-guard/programs.json`, and sources the integration from
`~/.zshrc` so it loads on every WSL shell start.

## Daily use

Launchers are wrapped so they self-register. Your aliases are unchanged in
behavior, just tracked:

| start | continue | tool |
|-------|----------|------|
| `cc`  | `ccc`    | claude |
| `oc`  | `occ`    | opencode |
| `hsi` | `hsr`    | hermes |
| `psi` | `psi-c`  | pi |
| `codex-run` | `codex-res` | codex |
| `an`  | `anc`    | ante (memory-based; `anc` == `an`) |

Run as many as you like, including several `cc` in one repo — each gets its own
sentinel.

After a crash, open one WezTerm tab and run:

```bash
cgr            # crash-guard restore: prints the plan, asks once, rebuilds tabs
cgs            # crash-guard status: running / stale / restorable
```

Useful flags:

```bash
crash-guard restore --dry-run        # plan only, spawn nothing
crash-guard restore --select         # pick 1,3-5
crash-guard restore --repair         # fix crash-truncated session files
crash-guard restore --no-spawn       # print the cd && resume commands instead
crash-guard restore --include-stale  # also restore same-boot stale sentinels
```

## Config

`~/.config/crash-guard/programs.json` — per-tool `continue` / `resume_by_id`
commands (with your `rtk` prefix + flags), session-store locations, the env
patterns captured per launch, and a `restore.pre` hook that runs
`_proxy_stack_auto_start` before reconnecting.

## Notes

- Resume flags verified 2026-06: claude (`-c` / `-r <id>`), codex
  (`resume --last` / `resume <id>`), opencode (`-c` / `-s <id>`), hermes
  (`--continue` / `--resume <id>`), pi (`-c` / `--session <id>`).
- ante (`@earendil-works`/extensible runtime) has **no** session-resume flag —
  `ante repl` auto-injects memory context, so restore just relaunches in the
  directory.
- Exact multi-same-directory restore is precise for claude, best-effort for
  codex; everything else degrades to CWD-first continue with a clear note.
