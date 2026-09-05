# Changelog

## [Unreleased]

### Added
- **`crash-guard recover`** — one entry point for crash detection. Reads the close markers harnesses actually write (omp's `session_exit` record, hermes' `ended_at`/`end_reason` in `state.db`) instead of inferring a crash from file mtimes. `--restore` reopens chosen sessions in tmux, each in the directory it was running in, read from the session's own log rather than reconstructed from the store's `<cwd-slug>` directory name (which is ambiguous: `-code-crash-guard` is equally `/code/crash/guard` and `/code/crash-guard`).
- **`crash-guard recover --compare`** — runs the marker and forensic detectors together and prints where they disagree, so the answer does not depend on which binary you happen to run. On the reference machine this surfaced a false positive: cg-forensic's sole result ("three converging signals, 5s before the crash") carries a `session_exit` record, so it had closed cleanly.
- **`bin/cg-session-end`** — a Claude Code `SessionEnd` hook that appends omp's exact `session_exit` marker, giving Claude the same definitive tell. It can only rule a session *out*, never in, since transcripts written before the hook existed lack the marker too. Every failure path exits 0; a hook that raises on shutdown is worse than a missing marker.

### Changed
- README documented one of seven tools in `bin/`. It now covers all of them, states which harnesses each half of the system actually reaches, and names the six with no scanner yet (`qwen`, `gemini`, `opencode`, `kilo`, `jcode`, `agy`).
- `.gitignore` now covers `.claude/` in-repo. It had been ignored only via a machine-global excludes file, so a fresh clone elsewhere would have offered to commit it.

### Removed
- `staged-for-deletion/` (17 `.pyc` and pytest-cache files staged by an earlier commit and never actually deleted), `archive/maximum-iterations-10-…/` and `.ralph-runner/` (a Ralph loop whose own summary records `status: timeout`, `progress=false` on all eight iterations and "No changed files recorded"). All three held zero git-tracked files. 6.3M → 5.1M.

### Fixed
- **Ghostty spawn no longer reports false success on native Linux.** `ghostty_spawn` used the nonexistent `+new-window` CLI action and returned "ok" the instant `Popen` started, so a window that opened and immediately died was logged as `restore_ok`. Now spawns with plain `ghostty -e` (works in both single-instance and standalone modes) and verifies the child didn't exit non-zero before reporting success.
- **Ghostty pre-flight was theater.** It probed `ghostty +list-windows` (not a real action), silently fell back to `--version`, and always "passed". Replaced with an honest `--version` liveness check.
- **Ghostty → tmux rescue fallback.** When a ghostty spawn genuinely fails (now detectable), the session drops into a detached tmux session instead of vanishing — the rescue path for "ghostty can't open windows right after a crash". Mirrors the existing tmux → ghostty fallback.
- **Stale prior-boot sessions are auto-expired.** On native Linux a `boot_id` change is usually a normal reboot, not a crash, so crash-guard was resurrecting week-old ghosts. Prior-boot crash groups older than `restore.stale_after_secs` (default 24h) are now hidden by default; `--all` reveals them and history browsing is unaffected.
- Reframed `--help` description and docs as "Linux (native + WSL2)" instead of WSL2-only.
- Fixed `build_plan` grouping: each session now gets its own plan item instead of collapsing multiple sessions in the same directory into one plan item. This ensures all sessions in a crash group are restored when selecting "all sessions".
- Added 30-second timeout to `wez_spawn` subprocess call — prevents indefinite hang when WezTerm is overwhelmed by too many simultaneous spawns.
- Increased default `spawn_delay` from 0.5s to 2.0s, max allowed from 30s to 60s — prevents terminal freeze when restoring many sessions.
- Fixed group sorting: same-boot stale sessions (now called "disconnected") appear first in restore menu, ahead of old-boot crashed sessions — users see their current work first.
- Lowered multi-session group threshold from 3 to 2 — more crash groups shown by default, fewer hidden.
- Removed single-session group suppression — all recoverable sessions now visible in restore menu without needing `--all`.
- Renamed "stale" to "disconnected" in user-facing output (`status`, `restore`, `prune` commands) — "stale" was misleading; these are same-boot sessions worth recovering.
- Added `rtx` to `known_wrappers` set — handles typo'd `rtx` sentinel key (was being used for hermes).

### Added
- Added `append_history(flush=True)` with `os.fsync()` for restore-related events so the log survives OOM kill.
- Added `_system_snapshot()` helper: logs `mem_total_kb`, `mem_avail_kb`, `live_sentinels`, `user_procs` in `restore_start`/`restore_done` events for crash diagnosis.
- Added `restore_start`/`restore_attempt`/`restore_ok`/`restore_fail`/`restore_done` history events for full restore traceability (replaces single `restore` event).
- Added `restore_start` includes full plan summary (key, cwd, inv_id, argv, tier per session) and system snapshot.
- Added session duration tracking: `cmd_stop` and `archive_sentinel` now compute `duration_secs` from `launched_at`.
- Added session duration display in recovery groups (e.g. `(8h35m)`, `(51s)`).
- Added ephemeral session filtering: sessions that ran < 5 seconds are never shown in restore groups (they're single-shot commands that exited immediately, not worth restoring).
- Added `_last_restore_diagnostic()` and enhanced `cmd_notify` to show a one-line diagnostic after a restore attempt, including memory pressure warnings (e.g. "critically low memory before crash: ~500MB available").
- Added clarifying restore output: "archiving old sentinel(s) and spawning fresh sessions (new inv_id per restore)" so the archive/new-session relationship is clear.
- Added command-existence check before spawning: warns when the first token of restore argv is not on PATH.
- Added `--include-closed` flag (opt-in) to show cleanly closed sessions in recovery groups.
- Added `crash-guard metrics` command: Prometheus-format metrics (counters, gauges, percentiles) for Prometheus scraping.
- Added `crash-guard analytics` command: structured JSON with pre-computed stats (session distributions, restore rates, memory, tool usage, boot periods).
- Added `ANALYTICS.md` — full documentation of all data streams, 12 pre-built visualization use cases, Grafana dashboard JSON, and web UI integration guide.
- Added `grafana/crash-guard-dashboard.json` — importable Grafana dashboard with 10+ panels covering all key metrics.
- Added terminal backend restore support for tmux, Ghostty, kitty, Windows Terminal, and existing WezTerm behavior.
- Added `crash-guard restore --terminal` to force a restore backend when auto-detection is not desired.
- Added MIT license and repository ignore rules for generated/local files.
- Added Kilo continue restore support.
- Added `cgr-archive` alias for retrying archived restore records through Ghostty/tmux.
- Added append-only durable session history with grouped recovery via `crash-guard history`.
- Added `crash-guard restore --group` and `--item` for restoring older groups or individual sessions.
- Added `crash-guard excise` dry-run cleanup with explicit confirmation for old history removal.
- Added `cgh` alias for history.
- Added no-argument `crash-guard` restore picker behavior.
- Added OS boot-period browsing to `crash-guard history`.
- Added `crash-guard restore --boot` for restoring groups from older boot periods.
- Added colorized terminal headings and an ASCII project title for interactive recovery views.
- Added `restore.spawn_delay` config (default 0.5s) to prevent OOM when restoring multiple sessions.
- Added recursive config merge so user configs receive all new default keys automatically.
- Added terminal backend validation before spawning (WezTerm, Kitty, Ghostty, Windows Terminal).
- Added troubleshooting section to README.
- Added config documentation for `spawn_delay`, `use_sentinel_argv`, and all program options.

### Changed
- Reworked README structure around install, restore workflow, terminal backends, config, and limitations.
- Changed Ghostty restore behavior to use tmux windows in the current Ghostty tab instead of opening Ghostty OS windows (was still opening new OS windows).
- Clarified that tmux restore uses tmux windows, not split panes.
- Changed default restore to derive targets from durable recovery groups instead of only currently crashed live sentinels.
- Changed `history` from a flat group list into a boot-period browser with number/arrow-key selection.
- Changed interactive restore into a two-step arrow-key picker for recovery group and session selection.
- Moved executable, shell integration, and installer into `bin/`, `shell/`, and `scripts/` for a cleaner repository root.
- Changed `scripts/install.sh` to install files only and leave shell rc edits to documented setup steps.
- Updated ASCII banner to clear "CRASH GUARD" block letters.
- Improved installer with verification, python3 check, and binary test.
- Improved `cg_run` with crash-guard PATH validation.
- Improved excise to process history line-by-line (memory efficient).

### Fixed
- **Fixed catastrophic OOM crash on restore**: Ghostty backend was opening new OS windows via `+new-window` instead of using tmux tabs inside the current window. When restoring multiple crashed AI-agent sessions (e.g. claude+hermes+codex), each got a new Ghostty OS window running a heavy agent, rapidly exhausting memory and crashing the desktop session. Now auto-detects when running in Ghostty and prefers tmux (with Ghostty as fallback only when tmux is absent).
- **Fixed `ghostty_spawn` blocking the spawn loop**: Changed from `subprocess.run` to `subprocess.Popen` so the spawn loop doesn't block until the inner command exits.
- **Fixed default restore showing all-time sessions**: Default `--boot` now filters to the current boot ID only, so sessions from old boots don't clutter the restore list.
- **Fixed archived sessions appearing in default restore**: Archived sentinels (already restored) no longer show up as restore targets. Use `--from-archive` to include them.
- **Fixed `--no-closed` flipped semantics**: Replaced with `--include-closed` (opt-in, default False) so closed sessions don't clutter the default restore view.
- Fixed restore planning for wrapper-keyed sentinels such as `rtk` by inferring the concrete tool from recorded argv.
- Fixed default config merging so existing user configs receive new built-in program entries without overwriting local entries.
- Added `restore --from-archive` so sentinels archived by a failed restore can be recovered.
- Fixed archived failed-restore sessions being split apart by preserved file mtimes; archive filename timestamps now group them correctly.
- Fixed empty argv handling in sentinel restore (returns key as fallback).
- Fixed spawn_delay validation (clamps to 0-30s range).
- Fixed config deep merge for nested sections (terminal, restore, wezterm, etc.).
- Fixed ghostty terminal backend detection: `_env_is("ghostty")` now correctly adds "ghostty" to backend candidates instead of "tmux".
- Fixed `--terminal ghostty` alias mapping: removed erroneous `"ghostty": "tmux"` alias that forced tmux backend when ghostty was requested.
- Fixed `tmux_attach` auto-attach behavior: now only attaches when already inside tmux (`ctx.get("inside")`), not when creating new sessions from other terminals.
- Fixed `select_group_records` to handle KeyboardInterrupt/EOFError during interactive selection, ensuring terminal state is restored.
- Added `cg_run` shell integration validation before spawning sessions, with clear error message if not sourced.
