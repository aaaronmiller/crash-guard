# Changelog

## [Unreleased]

### Added
- 2026-10-13: Recovery also reads interactive cut-off sessions from the history index for additional harnesses. Missing or stale indexes refresh from native evidence, additional store readers resolve watcher ids, and OpenClaw resumes name the session.
- 2026-10-13: `cgr` groups recovery by boot end and window-close events, exposes group metadata in JSON, supports whole-group selection, and collapses groups older than seven days unless expanded with `--expand-old` or `o`.
- **`cgr --save`, for a planned restart.** It records every agent running on a terminal, each with its session id (Claude's pid file; an id on the command line; otherwise the newest session in its directory for codex, omp and hermes), in `saved.json`. After any kind of shutdown, `cgr` lists each saved session once its process is gone and reopens the ones you pick. On 2026-10-10 it saved the four live sessions with their exact ids. Pinned by two tests, one against a real process in a pty.
- **`cgr --history` and `sessions.db`: every session from every harness.** It reads the stores of claude, codex, omp, pi, hermes, jcode, muse, gemini, qwen, opencode/kilo and openclaw into one SQLite table, with id, folder, harness, start, last activity, size, model, branch, first and last prompt, the reopen command, a status (`running`, `closed`, `cut off`, `unknown`) with its evidence, and `mid_turn` (the record stops on a prompt, tool step or error). The `audit` view lists the sessions you ran that were cut off or left mid-turn. Rows outlive their transcripts (`in_store` = 0), and the watcher refreshes the database daily in a low-priority child. First run: 1,455 sessions, 383 to audit, 10.6 s warm. Theory, per-harness signals and limits: `docs/design.md` section 17. Pinned by three tests.
- A session named on its command line (`codex resume <id>`, `omp --resume <id>`) now resolves to that id, for every harness. Before, codex took the newest thread in the directory, which on 2026-10-10 was one of its own subagents. Pinned by `test_a_session_named_on_the_command_line_is_the_one_reopened`, which fails on the previous code.
- **`cgr` is now `bin/cgr`, a new tool, not an alias for `crash-guard restore`.** After the 2026-10-09 WSL VM death killed a bare `claude` and a bare `codex`, `cgr` reported "no sessions to restore". The ledger only sees `cg_run` launches, and its fallback missed the codex session and listed six unrelated ones. The new tool reads the records each harness already writes, so it needs no wrapper or daemon. Claude: stale `~/.claude/sessions/<pid>.json` (deleted on any exit Claude sees), plus `SessionEnd` reason `other` on SIGHUP. Codex: no `Agent loop exited` line in `logs_*.sqlite`. omp: `session_exit`. hermes: `ended_at`. By default it lists only sessions from the previous and current boot, and it skips headless `claude -p`/SDK runs, codex `exec` runs and subagents. The picker uses arrow keys, and chosen sessions reopen in new tabs of the current WezTerm, Windows Terminal or tmux window. On 2026-10-09 it found exactly the two dead sessions.
- **`cgr --watch` and the `cgr-watch` user service.** It catches closed windows for every harness without hooking anything. It lists `/proc` every 2s and records each agent on a terminal with its directory, argv and the command line typed to start it, matched by timestamp in `~/.zsh_history` at launch, before zsh's duplicate removal can delete the entry. When an agent exits, a dead terminal shell means the window closed, and a live shell means you exited. The per-boot live set says what was running when a machine died. Measured cost: 0.11% CPU averaged over 9 hours (33.9 CPU-seconds), and 18 MB. Verified live: a killed window was recorded for codex and opencode, while Ctrl-C with the shell surviving was not.
- **Reopening uses what you typed.** Aliases, flags and proxy variables are kept, old session flags and typed prompts are dropped, and the harness's resume flag is appended. Resume flags for 15 harnesses come from their own `--help`. Restored tabs carry `CGR_LAUNCH`, so a session reopened twice keeps its original launch line.
- The once-per-boot login notice now calls `cgr --notify`, so it reports sessions from bare launches too.
- A zsh preexec/precmd hook was briefly part of this change and was removed the same day: it ran on every command typed. The watcher replaces it.
- **`docs/design.md`, the theory of operation.** It covers the goals, the four ways a session ends and what each leaves behind, both detection sources, how the command is rebuilt, and how tabs are reopened. It also records twelve alternatives, each with its evidence and the goals it fails, the known limits, an audit procedure that re-checks every claim, and an improvement backlog.
- **`crash-guard recover`** — one entry point for crash detection. Reads the close markers harnesses actually write (omp's `session_exit` record, hermes' `ended_at`/`end_reason` in `state.db`) instead of inferring a crash from file mtimes. `--restore` reopens chosen sessions in tmux, each in the directory it was running in, read from the session's own log rather than reconstructed from the store's `<cwd-slug>` directory name (which is ambiguous: `-code-crash-guard` is equally `/code/crash/guard` and `/code/crash-guard`).
- **`crash-guard recover --compare`** — runs the marker and forensic detectors together and prints where they disagree, so the answer does not depend on which binary you happen to run. On the reference machine this surfaced a false positive: cg-forensic's sole result ("three converging signals, 5s before the crash") carries a `session_exit` record, so it had closed cleanly.
- **`bin/cg-session-end`** — a Claude Code `SessionEnd` hook that appends omp's exact `session_exit` marker, giving Claude the same definitive tell. It can only rule a session *out*, never in, since transcripts written before the hook existed lack the marker too. Every failure path exits 0; a hook that raises on shutdown is worse than a missing marker.

### Fixed
- 2026-10-13: Final recovery checks fix generic pi/qwen entrypoints, flagged initial-prompt replay, one-shot Qwen queries and stale one-shot saves. Missing historical working directories no longer count as successful tab recovery. Final suite: 112 tests and 35 subtests.
- 2026-10-13: `cgr` filters CLI-specific one-shots from old watcher records, dates live sets by their boot end, resolves agy from a unique presence lock, checks history against argv, honors short resume flags, bounds store lookups by death, and retains successful WezTerm spawns when tab titling fails.
- 2026-10-13: `cgr` removes single-word launch prompts and Claude's `-resume` typo when rebuilding resume commands. Value-taking options keep their values, including quoted spaces. Four regression tests fail on the old code.
- **An empty boot after a crash no longer hides it.** On 2026-10-13 the WSL 3.0.3.0 updater stopped the VM at 05:05 with six agents running (two claude, codex, agy, two omp), booted it at 05:06 to finish installing, and let it idle out at 05:12. At 12:42 `cgr` said "nothing died": its window began at the previous boot, which was those five empty minutes, so the watcher's live set (last changed 2026-10-12 18:41) and every native record (last written by 04:25) fell outside it. `crash_boot_start()` now steps over each boot the watcher saw end with no agent running and nothing saved; a boot it did not see stops the walk. Pinned by `test_an_empty_boot_after_the_crash_does_not_hide_it`, which fails on the previous code with an empty list, and `test_a_boot_that_went_down_with_agents_ends_the_look_back`.
- omp writes `session_exit` on SIGHUP and SIGTERM too, naming the signal (14 `sighup` and 5 `sigterm` of 129 transcripts on 2026-10-10). `cgr` read any `session_exit` as a deliberate exit, so a closed omp window was missed without the watcher, and the watcher's record of it could not be matched to its id. Now `sighup` is "window closed" and `sigterm` is "killed", as Claude's `other` already was. Pinned by `test_omp_closed_by_a_signal_is_cut_off_and_dispose_is_not`, which fails on the previous code.
- The old `cg_run` trap logged SIGHUP (a closed window) as a clean close. A closed terminal could therefore never be restored. `cgr` treats a closed window as a death.
- `cgr` no longer lists a session that is running again. A Claude session resumed by hand with `claude --resume <id>` writes its `~/.claude/sessions/<pid>.json` late or not at all (11 minutes in, only a `.key` file existed), so `cgr` treated it as dead and would have opened a second copy. A session whose id is on the command line of a running agent is now skipped. Found live on 2026-10-09 and pinned by `test_session_named_on_a_running_command_line_is_not_listed`.
- `cgr` state is now owner-only: `~/.local/state/crash-guard/` and `watch/` are `0700`, and every file is `0600` (live sets, `deaths.jsonl`, `watch.pid`, `cgr-handled.json`). They hold argv, working directories and typed command lines, and they had been created with the default umask (`0775`/`0664`), against the rule `history.jsonl` follows. Older loose modes are repaired. Explicit modes rather than a process umask, which tabs that `cgr` opens would inherit. Pinned by `test_state_is_owner_only_and_loose_files_are_repaired`.
- `cgr` dates launches and boots by a clock that no longer slides. `btime` (wall clock minus uptime) drifted about 100 seconds per hour in WSL. Measured 2026-10-10: 18 minutes after 12 hours of uptime. Three effects followed. The watcher read `btime` once, at its own start, so hours later every new launch was dated about 15 minutes off and lost its typed line. A watcher restart re-dated running agents from their age and matched a mistyped `claude -resume` instead of the real `claude --resume 9a3daff8...`, which the 08:14 crash then reopened. Read late in a boot, `btime` would also have counted sessions busy just before a crash as this boot's and dropped them. Now the watcher reads `btime` afresh for each new process, keeps what it recorded at launch across restarts, and this boot's start is the first reading, kept in `watch/boot-<boot_id>`. Pinned by three tests, each written first and failing.
- **Security:** `cgr` trusts only its own account. Two issues came from an automated review of the pushed commit:
  - The watcher recorded any account's agent from its argv. Another local account could run a process named `claude` with flags of its choosing, such as `--mcp-config=...`, and picking it in `cgr` would run those flags as you. Now only processes you own are recorded or counted as running, and only state files you own are read.
  - Titles, which are prompts and model-written thread names, along with typed lines and paths, went to the terminal raw. Now control characters are shown as `?`.

  Pinned by three tests, each written first and failing.

### Changed
- README rewritten around `cgr`: what it does, how it works, why this design, supported harnesses, install, operating the watcher, files, troubleshooting. The `cg_run` ledger documentation moved to a "Legacy" section, and the instruction to define `alias cgr='crash-guard restore'` is gone, since that alias shadows the program. Also corrected: the ledger's OOM advice now says to raise `restore.spawn_delay`, not reduce it.
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
