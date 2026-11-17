# MVP progress: crash-guard (`cgr`)

Branch `cgr-full-yes`, from origin/main 3c0f65c. Committed only; nothing pushed.
Date: 2026-11-17. Suite command (all results below):
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q -p no:cacheprovider tests`

## Done

### 1. `cgr --full --yes` reopened sessions that were still running (R17 B077)

Cause: `--full --yes --day D` (or `--group N`) reopens every row of the date.
`full_rows()` keeps every interactive session with an id, and a `running` row is
one of them; nothing between the list and `reopen()` looked at the status. The
listing was right per row (`running: process alive`) but the date header counted
running (and closed) rows as crashes (`3 crash`).

Reproduced first, against the unmodified `bin/cgr`:

```
$ pytest -k "full_yes_does_not or full_listing_shows"
FAILED test_full_yes_does_not_reopen_a_session_whose_agent_is_alive
  AssertionError: Items in the first set but not the second: 'named', 'pidfiled'
FAILED test_the_full_listing_shows_a_running_session_as_running_not_recoverable
  ... [2026-11-17, 4 sessions: 1 window, 3 crash] ...   (expected: 2 running, 1 window, 1 closed)
```

Fixtures: real fake agent processes (`claude.py --resume named`, and a sleeper
with a `~/.claude/sessions/<pid>.json` pid file), plus transcripts, in a temp home.

What changed (`bin/cgr`):
- `held_sessions()` / `is_held()`: one definition of "a live agent holds this
  session" (Claude live pid files, `running_sessions()` ids, ids on running
  command lines). `build_index()` now uses it for the `running` status, so the
  listing and the guard cannot disagree.
- `drop_running(items, rescan=True)`: drops rows whose status is `running` and,
  with `rescan`, anything `/proc` shows as held right now. Names the skipped
  sessions on stderr: `cgr: not reopening N sessions, still running: ...`.
  Skipped sessions are not marked handled.
- Applied on every path that opens tabs: `show_history()` (the `--yes` count),
  `reopen_history()` (`--history`/`--full` picks and `--yes`), and `reopen()`
  itself (so `cgr`, `cgr --yes [--group N]`, and both pickers re-check at the
  moment of opening, which covers a session resumed while a list was open).
- `choose_history()`: a running row is drawn `[-]`, space does nothing on it,
  `a` skips it.
- `history_kind()` / `history_group_label()`: `running` and `closed` are their
  own kinds in a date header (`4 sessions: 2 running, 1 window, 1 closed`) and
  do not set its color.
- Unchanged on purpose: `--full --yes` still reopens closed sessions (owner
  decision recorded in CHANGELOG).

Tests (all in `tests/test_cgr.py`):
- `test_full_yes_does_not_reopen_a_session_whose_agent_is_alive`
- `test_the_full_listing_shows_a_running_session_as_running_not_recoverable`
- `test_a_running_session_does_not_set_the_color_of_its_date`
- `test_reopen_asks_again_so_a_list_gone_stale_cannot_start_a_second_copy`
- `test_the_history_picker_shows_a_running_session_but_cannot_pick_it`
- `test_history_yes_does_not_reopen_a_session_whose_agent_is_alive` (the
  non-full cut-off list was already safe; this pins it)

Fail-before check: the first four fail against the unfixed `bin/cgr` and pass
after (the picker test runs the picker in a subprocess, so it was not run
against the old script; the last one passes on both, by design).

End to end, real CLI in a subprocess, fake home, PATH stripped so no tab can open:

```
$ cgr --full --yes --day 2026-11-17
Reopening 2 sessions from 1 date
run these yourself:
  cd ~/work && claude --resume gone
  cd ~/work && claude --resume done
[stderr] cgr: not reopening 2 sessions, still running: claude pidfiled, claude named
```

Docs: README (history section, troubleshooting row), `docs/design.md` (17.3,
17.7 item 9, new 18.7, audit step 0 counts), CHANGELOG (Fixed, and the earlier
"owner decision" note amended).

### 2. Test-environment flake: `test_a_card_shows_title_span_duration_tokens_and_id`

Finding that differs from the brief: on this host the failure was not the
watcher line. The line `cgr: the watcher is not running ...` prints on every
run, because the test uses a fake state dir; no assertion looks at it. The test
failed because its timeline hangs off the host's real boot and clock: with the
host up over a day the card reads `(1d 0h 40m ago)` and the regex
`\((\d+h )?\d+m ago\)` does not match. A boot within three hours of midnight
would also have made the card print two dates. Both are fixed.

- `pin_host(boot)`: fixed boot start and a fixed clock (40 minutes after the crash).
- `watcher_up()`: leaves the state a running watcher leaves (`watch.pid` for this
  boot naming a live process whose argv has `--watch`).
- The card assertions are unchanged; one is added: no "watcher is not running"
  text precedes the card.
- `test_cgr_warns_only_when_the_watcher_is_not_running` pins the warning in
  its three states (absent, running, a watcher of another boot).

### 3. Other baseline failures, fixed so the suite is 0 failed

Baseline on origin/main: `6 failed, 163 passed`. Besides the card test:
- 4 pty picker tests (`test_picker_*`, `test_history_picker_a_...`,
  `test_history_picker_f_...`): the test pty had no window size, so cgr's
  120-column fallback cut the footer ("enter reopen") off unless `COLUMNS` was
  set. `sized_pty()` sets a real window size and the matching env.
- `tests/test_forensic.py::test_marker_patterns_match_real_files`: asserted that
  this machine has a live omp client file. Now
  `test_marker_patterns_match_the_files_each_harness_writes` (files made in a temp
  home, also exercises `cwd_from_omp_marker`), plus
  `test_marker_patterns_match_real_files_where_there_are_any` (skips when no
  marker is open). This changes what the old test asserted; it is the one
  assertion I loosened, and only because it measured the host.
- `setUp` pins `shutil.get_terminal_size` to each call's own default, so the
  layout tests no longer depend on the caller's `COLUMNS` or terminal.

## Verification

```
$ PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q -p no:cacheprovider tests
177 passed                       (three runs in a row, 0 failed, no flakes)
$ TZ=Pacific/Auckland ...        177 passed
$ COLUMNS=50 LINES=8 ...         177 passed
```

Counts: 140 cgr tests, 177 in all (origin/main had 133 and 169: 163 passing, 6 failing).

## Attempted, not done

- `crash-guard restore` (the old ledger) is another path that reopens
  sessions. Read, not changed, not tested: `restore_groups()` skips sessions its
  own history marks `running`.

## Blockers and limits

- Running detection is only as good as its three signals. Not seen as running:
  a session with no id; a claude started with `--continue` before its pid file
  or a touched transcript shows which session it took; an agent `harness_of()`
  does not recognize. Written up in `docs/design.md` 17.7 item 9 and README
  troubleshooting.
- Observation, not changed: `history_kind()` used to call any status other than
  saved/window a crash, so `unknown` rows still count as `crash` in a date header.
- I could not delete my scratch files without an approval prompt. They are
  untracked and not committed: `.base-cgr`, `.fixed-cgr`, `.run-base.py`,
  `.e2e.py` in the repo root (and a temp home under the system temp dir).
  Remove them before collecting if the working tree is bundled as-is.
- The repo's own `tasks.md` is a finished 2026-10-17 list ("No known open
  issues") and there is no crash-guard dossier in `~/ld-context`, so there was
  no next task to continue with.

## Next

Nothing open on this repo. Candidates for the owner: decide whether
`--full --yes` should also skip closed sessions (currently reopened by design),
and whether `unknown` rows should get their own date-header kind.
