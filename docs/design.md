---
date: 2026-10-09 00:00:00 PT
author: Ice-ninja
model: claude-opus-5-5
tags: [crash-guard, cgr, design, theory-of-operation, session-recovery, audit]
---

# cgr: theory of operation

This document explains how `cgr` decides which agent sessions were cut off and
how it brings them back. It also records why each mechanism was chosen over
the alternatives, and how each one serves what the user asked for. It is
written for a later audit: every claim says whether it was measured on this
machine (and how), read from the code, or only reasoned. To check the design,
follow the audit procedure in section 12. To change it, start from the known
limits in section 11 and the backlog in section 13.

Code references name functions in `bin/cgr` rather than line numbers, which
drift. State as of 2026-10-10. Sections 16 and 17 (2026-10-10) cover the two
later features: saving the running set before a planned restart, and the
database of every session from every harness.

Contents:

1. What the user asked for
2. Why the previous `cgr` failed
3. The problem, stated precisely
4. Architecture
5. Source 1: what each harness already records
6. Source 2: the watcher
7. Putting the sources together
8. Rebuilding the launch command
9. Reopening
10. Alternatives considered
11. Known limits and failure modes
12. Audit procedure
13. Improvement backlog
14. Files and record formats
15. Relationship to the rest of crash-guard
16. A planned restart: `cgr --save`
17. Every session in one database: `cgr --history`
18. Recovery corrections, 2026-10-13

## 1. What the user asked for

The design answers four messages. Three are from the evening of 2026-10-09,
quoted exactly as typed (voice-transcribed). The fourth is the standing goal
from June 2026.

> "this completly failed just now - i had a claude code and a codex session
> running - something closed the terminal, illing both sesions - i tried cgr
> on restart and it did not identify any sessions - fix the problem , thius
> should be a simple task - may rquire a completely new approach to solve the
> problem, i'm not happy wiut anything this program does currently."

> "you put a hook on EVREY command? that seems like the wrong way to handle
> thigns - we also want the systme to work with all the other harnessess on
> the system (hermes, openclaw, kilo, agy, jdcode, opencode,, ante, among
> others) - figure out a less invasive method to resolve the issue"

> "also you shuold be able to determine if any launch arguments were used for
> the various harnesses by examining command history - this shouldn't be that
> hard of a task"

> (2026-06-13) "a list of all 12 folders that were open when the crash
> occurred … and finally if I could use the cgr command and launch all 12
> sessions that would be great."

A fifth message, on 2026-10-10, asked for two more things (sections 16 and
17):

> "ok now whate i want is a flag that enables the system to proactively
> record the current running harnessess so if i want ot do a restart , i can
> easily shutodwn and get back to the current state) - also i want ot be able
> ot use the heuristics we have to find historicla sessions which were crashed
> or incomplete (it shuold create a master list of all sesions from all
> harnesses, and flag those which were closed unexpectedly, so i cant then
> audit thoses seessions and determine if there are unfished taskes associated
> with said session - it should record as much in fo as possible in a
> sdatabase (session id, folder, session harness type, date,/time , size (if
> easy), etc."

Those messages give ten goals. Section 10 and the traceability table in
section 11 refer to them by number.

| # | goal | source |
|---|---|---|
| G1 | Find every agent session that was cut off, whether the machine died or the window closed. | message 1 |
| G2 | List nothing the user ended on purpose, and nothing unrelated. | message 1 (the old fallback listed six unrelated sessions) |
| G3 | Work for bare launches (`claude`, `codex`), with no launcher prefix to remember. | message 1 (both sessions were bare launches) |
| G4 | Be non-invasive: no hook on every shell command, no hook on every harness action. | message 2 |
| G5 | Cover every harness on the machine: claude, codex, hermes, openclaw, kilo, agy, jcode, opencode, ante and the rest. | message 2 |
| G6 | Reopen with the launch arguments originally used, taken from command history. | message 3 |
| G7 | Stay simple: one command, pick, reopen. | messages 1 and 3 |
| G8 | Each session comes back in its own directory, as the same conversation where possible, in a new tab. | standing goal, 2026-06-13 |
| G9 | Before a planned restart, record what is running with one flag, so the same set comes back afterwards however it was shut down. | message 5 |
| G10 | One database of every session from every harness, with id, folder, harness, times and size, flagging those that ended unexpectedly or were left unfinished, for audit. | message 5 |

## 2. Why the previous `cgr` failed

**What happened.** At 20:26:25 on 2026-10-09 the WSL VM died. The systemd
journal for that boot ends at that second, and wtmp has no shutdown record for
it. The next kernel boot started at 20:35:26 (`uptime -s`), and wtmp logged it
at 20:37. Two sessions died:

- a bare `claude` in `/home/cheta`, session 9a3daff8, pid 5719, launched
  18:24;
- a bare `codex` in `/home/cheta/code/pareto`, thread 01a12388, launched 18:58.

The Claude session had run `skillshare sync` 14 seconds before the crash.
Whether that caused the crash is **unverified**.

At 20:37:47 the user ran `cgr`, which was then `alias cgr='crash-guard
restore'`. It reported nothing to restore. It failed in three ways:

1. **It only saw wrapped launches.** `crash-guard restore` reads the `cg_run`
   ledger, which only records launches that go through the `cg_run` wrapper.
   The user launches bare binaries, so the ledger was empty. On 2026-08-20
   only 10 of 83 aliases routed through `cg_run` (12%), and none of the daily
   launchers did.
2. **Its fallback guessed.** With an empty ledger it fell back to marker and
   mtime heuristics (`cg-recover`). These missed the codex session and listed
   six unrelated sessions, including headless `claude -p` reviews and sessions
   weeks old.
3. **It filed a closed window as a clean exit.** The `cg_run` SIGHUP trap
   recorded a closed window as a normal stop, so a session lost to a closed
   terminal could never be restored even when it had been wrapped.

The August redesign had a measuring instrument, the `cg-snapshot` daemon. It
was not running either. Its newest snapshot is from 2026-09-02 11:47 PT
(18,743 files, 74 MB on disk), so it had been dead for five weeks and nobody
noticed. The 2026-08-19 decision had predicted exactly this when it called a
daemon "a running process that can die".

## 3. The problem, stated precisely

A recovery tool has to answer two questions:

- **Q1. Which sessions were cut off?** This means separating sessions that
  ended against the user's will from sessions the user ended.
- **Q2. How do you bring each one back as it was?** That needs the
  directory, the conversation id, and the command line that launched it.

### The four ways a session ends

Q1 is hard because the four ways a session can end leave different traces,
and two of them leave none.

| how it ended | what the kernel does | does the harness get to run its exit code? | what is left behind | the terminal's shell afterwards |
|---|---|---|---|---|
| **deliberate exit** (`/exit`, `/quit`, Ctrl-C, Ctrl-D) | nothing special | yes, fully | the harness's normal close records | alive, back at its prompt |
| **window closed** | hangs up the terminal and sends SIGHUP to the session leader (the shell) and the foreground process group | partly: it gets SIGHUP and usually runs some or all of its shutdown | often the same close records as a deliberate exit | dead |
| **killed** (SIGKILL, OOM killer) | ends the process at once | no | whatever was already on disk; no close record | usually alive |
| **machine death** (VM, kernel or power loss) | everything stops at once | no | whatever was already on disk; no close record; no shutdown entry in wtmp | gone with everything else |

Two conclusions shape the whole design.

1. **A harness's own records cannot separate "window closed" from
   "deliberate exit".** On SIGHUP, codex tears its thread down and logs the
   same line as `/quit` (measured, section 5.2). From inside the harness, a
   closed window looks like a normal exit. The decisive fact is outside the
   harness: whether the terminal's shell survived. Only an observer outside
   the harness can check that.
2. **Missing close records only mean something when paired with positive
   evidence that the session was alive at the end.** Treating "no stop
   record" as "crashed" produced 13,598 phantom crash records in the old
   ledger (decision of 2026-08-19). So every rule in `cgr` needs a positive
   signal, such as a pid file that was never removed or a live-agent list
   written while the agent ran. A missing marker on its own never counts.

### What "cut off" means for listing

`cgr` lists a session when it was running and nobody exited it. That covers
machine death, a closed window and a kill. A window the user closed on purpose
to end an agent is also listed: from outside, it cannot be told apart from an
accidental close. Section 11 covers why that trade-off is acceptable: `cgr`
asks before reopening anything.

## 4. Architecture

```
 while sessions run                         after a crash or a closed window
 ------------------                         --------------------------------
 /proc --every 2s--> cgr --watch            cgr
   (systemd user service cgr-watch)          |
     | live-<boot_id>.json  agents now       +-- collect()
     | deaths.jsonl         window closed    |     native records (claude, codex, omp, hermes)
     | watch.pid                             |     watcher records  (every harness)
     |                                       |     resolve() -> session id from each store
 ~/.zsh_history --read at launch--> typed    |     typed line from the watcher or history
                                             |     drop handled and running-again sessions
 harness stores (read only by cgr):          +-- command()  typed line + resume flag
   ~/.claude/sessions/<pid>.json             +-- choose()   arrow-key picker
   ~/.claude/projects/*/<id>.jsonl           +-- reopen()   a new tab per session:
   ~/.codex/state_*.sqlite, logs_*.sqlite                   tmux | WezTerm | Windows Terminal
   ~/.omp/agent/sessions/*/*.jsonl
   ~/.hermes/state.db
```

There are two independent sources of "this session was cut off", and either
one is enough to list a session:

- **Native records (section 5)** need nothing installed. They answer Q1
  completely for claude. For codex, omp and hermes they answer it for machine
  death.
- **The watcher (section 6)** watches every harness the same way. It is the
  only source that can tell a closed window from a deliberate exit for
  harnesses other than claude. It also captures the typed command line for
  Q2.

`cgr` itself is a short-lived command. It reads both sources, merges them,
and shows a picker. It never runs in the background; only the watcher does.

## 5. Source 1: what each harness already records

The rules below come from experiments run on 2026-10-09 against live
processes. They are private implementation details of each harness and can
change on any upgrade; section 12 says how to re-check them.

### 5.1 claude

**Signal: `~/.claude/sessions/<pid>.json`.** Claude Code writes this file for
each interactive process. It holds `pid`, `sessionId`, `cwd`, `startedAt` (ms)
and `procStart` (field 22 of `/proc/<pid>/stat`, the start time in clock
ticks), plus `entrypoint` (`cli` for a terminal, `sdk-cli` or `sdk-py` for
headless runs).

**Claude deletes the file on every exit path it gets to run, SIGHUP
included.** This was measured: in the probe, the file for pid 15901 was gone
4 seconds after `kill -HUP`. So a file that is still there, whose process is
gone, means Claude never ran its exit path, which only happens on a machine
death or a SIGKILL. `claude_pids()` treats a file as live only if its
`procStart` still matches a running process (a bare pid match would be fooled
by pid reuse; one leftover file on this machine, `1127.json`, names a pid that
now belongs to another process). Across 586 leftover files, about one `cli`
file per boot was left behind, which fits the "machine death leaves one"
reading.

**Signal: the `SessionEnd` reason.** On SIGHUP Claude fires its `SessionEnd`
hook with reason `other`. On `/exit` the reason is `prompt_input_exit`. Both
were measured. The pre-existing hook `bin/cg-session-end` (installed in
`~/.claude/settings.json` on 2026-09-04) appends
`{"type":"custom","customType":"session_exit","reason":...}` to the
transcript. `exit_reason()` reads the last such record after the last message.

**Verdict rules, in `claude()`:**

| evidence | verdict |
|---|---|
| exit reason `other` | **window closed** |
| no exit record, and a pid file whose process is gone | **machine went down** if the transcript was last written before this boot, otherwise **killed** |
| exit reason anything else | not listed: closed on purpose |
| no exit record and no stale pid file | not listed: no positive evidence |

**Filters:**

- Transcripts whose `entrypoint` is not `cli` (headless `claude -p`, SDK
  runs) are skipped. This is what removed the headless reviews the old
  fallback listed.
- Transcripts with no real user prompt are skipped.
- Sessions whose id is in a live pid file are skipped, because they have been
  resumed elsewhere.
- Sessions whose id appears on the command line of any running agent are
  skipped (`running_words()`). This was added late on 2026-10-09 after a live
  case. The user resumed 9a3daff8 by hand with `claude --resume
  9a3daff8...`, and the new process had not written its pid file: 11 minutes
  in, only a `<pid>.<hash>.key` file existed. Without the check, `cgr` would
  have offered to open a second copy of a running session.

**Limit: the pid file is not guaranteed.** That resumed process is an
interactive `cli` session with no `<pid>.json`. If the machine dies under a
session like that, no stale pid file is left behind, and claude's native rule
cannot see it. Only the watcher's live set (section 6.4) covers it. Why some
sessions skip the file has not been investigated (it may be written on the
first prompt).

**Why this signal and not the older one.** `cg-forensic` used
`~/.claude/security_warnings_state_<id>.json`, which is never removed (198
files against 20 sessions on 2026-08-19). A marker that is never removed
cannot say whether a session ended. The pid file can.

### 5.2 codex

**Signal: the teardown line in `~/.codex/logs_*.sqlite`.** Codex runs an
app-server daemon that logs per thread. On `/quit` it logs `Agent loop exited`
or `op.dispatch.shutdown` for the thread. About 60 seconds after a SIGHUP it
logs the same, because the daemon reaps the dead client. A VM death logs
nothing. All three cases were measured.

**Verdict rule, in `codex()`.** A thread is listed as **machine went down**
when all of these hold:

- it was updated since the window start;
- its newest log line is from before this boot;
- it has no teardown line within 120 seconds of its newest log line.

Threads touched during this boot are left to the watcher, because a live
thread has no teardown line yet either. Threads are taken from
`state_*.sqlite` `threads`. Rows with `source` `exec` or `mcp`, subagent
threads, and archived threads are excluded.

**Rejected: the thread writer lock.** The daemon holds
`~/.codex/thread-writer-locks/<id>.lock` while a thread is open, which looked
like a liveness marker. It is not durable. The next codex start deletes stale
locks, and a probe launch during the investigation deleted the lock of the
crashed thread 01a12388 (measured; the thread and rollout were intact).

**Limit.** Codex cannot tell a closed window from `/quit` on its own (both log
a teardown), so a closed codex window needs the watcher.

### 5.3 omp

omp ends a transcript with `{"type":"custom", "customType":"session_exit",
"reason": ...}` on every exit it gets to run. A transcript under
`~/.omp/agent/sessions/*/` that was last written before this boot and has no
such record after its last message is listed as **not closed** (`omp()`).

**Fixed 2026-10-10.** omp also writes the record when a signal ends it, and
names the signal: `sighup` when its window closed, `sigterm` when it was
killed. `cgr` had read any `session_exit` as a deliberate exit, so a closed
omp window was invisible without the watcher, and the watcher's own record
for it could not be matched to its id. Of 129 transcripts on 2026-10-10, 82
end `dispose` (a quit), 14 `sighup`, 5 `sigterm`, 1 `exit`, and 27 with no
record. `CUT_EXITS` now maps `sighup` to "window closed" and `sigterm` to
"killed" for omp and claude alike. Pinned by
`test_omp_closed_by_a_signal_is_cut_off_and_dispose_is_not`, which fails on
the previous code.

### 5.4 hermes

`~/.hermes/state.db` `sessions.ended_at` stays NULL for a session that never
closed. Rows with messages, not cron jobs, last active before this boot, are
listed as **not closed** (`hermes()`).

### 5.5 Why only these four

These four store a session id that `cgr` can read and a close signal that has
been checked. The other eleven harnesses in the table do not yet have both
verified, so they rely on the watcher (section 6), and they reopen with their
own "continue the newest session here" flag (section 8). Section 13 lists the
store readers worth adding.

## 6. Source 2: the watcher

`cgr --watch` (`class Watcher`, `watch()`) runs as the systemd user service
`cgr-watch`. It answers Q1 for every harness the same way, and it records the
command line for Q2.

### 6.1 One scan, every 2 seconds

`Watcher.scan()`:

1. Lists `/proc`.
2. Re-checks agents held over from the last scan (see 6.3).
3. For each tracked agent: if it is still alive (same pid, same start ticks),
   refreshes its Claude session id from its pid file, which follows `/clear`
   and `/resume`. If it is gone, applies the shell-liveness rule.
4. For each pid not seen before, calls `consider()`.
5. If anything changed, rewrites `live-<boot_id>.json` atomically (write to
   a temp file, then `os.replace`).

Only new pids are read. A process already looked at is never re-read unless
it is a tracked agent.

### 6.2 Which processes count as sessions

`consider()` and `harness_of()` accept a process only when all of these hold:

- **It belongs to this account** (`mine()`: the owner of `/proc/<pid>`).
  Another account chooses its own argv, for example
  `claude --mcp-config=/tmp/x.json`. If its process were recorded, picking it
  would run that as you. `running_words()` applies the same rule, so another
  account cannot hide your sessions by naming their ids. Added 2026-10-10
  after an automated security review.
- **It has a controlling terminal** (`tty_nr` is not 0). Daemons, pipes and
  background helpers have none. The same discriminator removed three false
  positives in the August snapshot work (two muse helpers, one Claude SDK
  helper).
- **Its program is a known harness.** That is the basename of `argv[0]`, or,
  for `node`, `bun`, `deno`, `python` and `sh` interpreters, the first
  non-flag argument with `.js`/`.mjs`/`.cjs`/`.py` stripped. `sh -c '...'` is
  rejected because it is a command string, not a script.
- **It is not a one-shot.** The first positional word is not a subcommand
  such as `exec`, `run`, `serve`, `app-server`, `mcp`, `login`, `update`,
  `doctor`, `auth`, `config`, `gateway` or `daemon`, and there is no `-p`,
  `--print`, `--version` or `--help`.
- **It is the outermost agent on its terminal.** Walking up through parents
  inside the same terminal session, no ancestor is already an agent. An agent
  started by another agent (a review, a subagent with a pty) is part of that
  session, not a session of its own.

### 6.3 The shell-liveness rule

This rule is the core of the watcher.

Every terminal tab or pane is a session in the POSIX sense. Its leader is the
shell the terminal started. When the window closes, the kernel hangs up the
terminal: the shell gets SIGHUP and exits, and the agent running under it gets
SIGHUP too. When the user quits the agent instead, the shell goes back to its
prompt and lives on. So when a tracked agent disappears, the watcher asks
whether that terminal's session leader died too:

| agent gone and ... | means | recorded? |
|---|---|---|
| the leader (same pid, same start ticks) is gone or a zombie | the terminal went away: **window closed** | yes, appended to `deaths.jsonl` |
| the leader is still alive | the user exited (or the agent was killed while the shell lived) | no |

Some details matter:

- **The leader, not the parent.** The agent's parent may be an alias step, a
  proxy wrapper (`xx`, `rtk`) or a node shim. The session leader is the
  terminal's shell however deep the nesting.
- **Ordering.** On a hangup the agent and the shell die at slightly different
  times. If the agent is gone and the shell still looks alive, the record is
  held for one more scan and re-checked, in case the shell is mid-hangup. If
  the agent outlives the shell (opencode lingers about 5 seconds after its
  terminal dies, measured), the shell is already gone when the agent goes, so
  it is recorded straight away.
- **Zombies count as dead.** A closed window's shell can sit unreaped for a
  moment, and it is no longer at any prompt.
- **Start ticks guard against pid reuse.** Liveness is pid plus start time,
  never pid alone.
- **`exec` keeps the leader.** Reopened tabs end with `exec $SHELL -l`
  (section 9). `exec` keeps the pid and the start time, so the leader's
  identity survives it.

**Verified live on 2026-10-09** with tmux windows on this machine:

- a `codex -m gpt-6-sol` window killed while running was recorded, with the
  typed flags;
- an opencode window started through the alias `oc-direct`, then killed, was
  recorded, with the alias;
- codex quit with Ctrl-C, shell surviving, was not recorded.

### 6.4 The per-boot live set

`live-<boot_id>.json` always holds the agents running right now. The boot id
comes from `/proc/sys/kernel/random/boot_id` and is new on every boot. So on
the next boot, the previous boot's file is exactly the list of agents that
were running when that boot ended. `watched()` reports those as **machine
went down**. It needs no shutdown hook, which is the point: a machine death
runs no hooks.

This also covers a deliberate `wsl --shutdown` or reboot with agents open.
Those sessions were cut off too, so listing them is correct.

### 6.5 Capturing what was typed

On this machine zsh runs with `EXTENDED_HISTORY` (each entry carries its start
time), `INC_APPEND_HISTORY` and `SHARE_HISTORY` (entries are written when a
command starts, from every shell), and `HIST_IGNORE_ALL_DUPS` (typing a command
again deletes the older copy). `~/.zshrc` lines 53-54 set these.

`typed_for()` picks the history entry closest before the process start,
within 300 seconds before it and 5 seconds after, whose last command starts
this harness either directly or through an alias. The window is wide because
an alias that first starts a proxy can take a while before the agent process
appears. Aliases come from the `alias` lines in `~/.zshrc` and
`~/.zsh_aliases` (`aliases()`). `history()` reads the last 1 MB of the file,
decodes zsh's "metafied" bytes and joins continued lines.

**Why capture at launch, not at restore time.** `HIST_IGNORE_ALL_DUPS`
deletes the older copy whenever the same command is typed again. It had
already deleted the 18:24 `claude` entry for the session that crashed on
2026-10-09, because `claude` was typed again later. The watcher reads history
within 2 seconds of the launch, while the entry still exists, and keeps it in
its own record. `cgr` falls back to a history lookup at restore time only for
sessions the watcher never saw.

**`CGR_LAUNCH`.** A reopened tab runs a reconstructed command, not what the
user typed. The tab exports `CGR_LAUNCH=<original typed line>`, and
`launch_env()` reads it from `/proc/<pid>/environ`. A session reopened twice
therefore keeps its original launch line instead of drifting.

**Dating the launch (fixed 2026-10-10).** History timestamps are wall-clock
time. A process's start is only known as ticks since boot, so the watcher
converts it with `btime`, which the kernel derives as the wall clock minus
uptime. In WSL those two clocks drift apart, so `btime` slides. Measured on
2026-10-10: it read 18 minutes before the boot's own wtmp stamp 12 hours into
the boot, about 100 seconds per hour, steadily.

The first watcher read `btime` once, at its own start, and used it for days:

- 9 hours in, every new launch was dated about 15 minutes wrong, and its
  history line fell outside the window.
- A restart re-dated running agents from their age, which carries the same
  drift. On 2026-10-09 at 23:29, a 22-second error matched the wrong history
  line: a mistyped `claude -resume` 43 seconds before the real
  `claude --resume 9a3daff8...`.
- After the 2026-10-10 08:14 crash, `cgr` reopened that session with
  `claude -resume --resume 9a3daff8...`. Claude accepted it, by luck.

Now:

- `consider()` reads `btime` afresh for each new process. The watcher sees a
  launch within 2 seconds, so the drift has no time to matter.
- A restarted watcher keeps the `started` and `typed` it recorded at launch
  (`self.seen`, keyed by pid and start ticks, loaded from the live set)
  instead of recomputing them.
- Pinned by `test_watcher_dates_a_launch_by_the_clock_now_not_when_it_started`
  and `test_watcher_restart_keeps_what_it_saw_at_launch`, both written first
  and failing.

### 6.6 Cost and supervision

- **Cost, measured.** 0.3% CPU in the first minutes. Over its first 93
  minutes the service used 4.7 CPU-seconds in 5,611 seconds (0.08% average),
  with 18 MB resident (`ps`, `/proc/<pid>/stat`). Overnight, 2026-10-09 23:29
  to 2026-10-10 08:26: 33.9 CPU-seconds in 32,228 seconds (0.11%), 17.9 MB
  resident.
- **Supervision.** systemd user unit with `Restart=always`, `RestartSec=5`
  and `Nice=10`. The user instance has `Linger=yes`, so it starts at boot
  without a login. `cgr` prints a warning when the watcher is not running for
  the current boot (`watcher_running()` checks `watch.pid`: the pid, its argv,
  and that it was written this boot). This answers the failure that killed the
  snapshotter.
- **Bounded storage.** `prune()` drops live files and death records older
  than 30 days at each start.
- **A bad read never stops it.** `watch()` catches any exception from a scan,
  logs it to the journal, and carries on.

## 7. Putting the sources together

`collect()` builds the list `cgr` shows.

1. **Window.** The default window starts when the previous boot started, read
   from the `BOOT_TIME` records in `/var/log/wtmp` (`previous_boot_start()`),
   minus 120 seconds of margin. The earlier explanation for that margin, that
   WSL stamps wtmp a minute or two after `btime`, was most likely the drift
   described in 6.5, seen in readings taken late in a boot. Without wtmp it
   falls back to 24 hours before this boot.
   This boot's start, the line between "before the crash" and "this boot",
   is `boot_start()`. It is `btime` as first read this boot, kept in
   `watch/boot-<boot_id>`. The watcher reads it as the boot starts, and later
   readings would have slid. Read 12 hours late, a fresh `btime` put codex
   threads busy in the last 18 minutes before a crash into this boot, so they
   were dropped (`test_a_late_reading_of_btime_does_not_move_the_boot`). `--all` removes the bound. Every process alive when the previous boot
   ended was started inside that boot, so this is the tightest bound that
   still catches them. It is also what keeps sessions from weeks ago out of
   the list, which the old fallback got wrong.
   **Empty boots are stepped over** (`crash_boot_start()`). "The previous
   boot" assumes one boot per crash, and on 2026-10-13 that failed: the WSL
   3.0.3.0 MSI stopped the VM at 05:05 with six agents running, booted it at
   05:06 to finish installing, and let it idle out at 05:12. At 12:42 the
   previous boot was those five empty minutes, every source dropped the six
   sessions as older than the window, and `cgr` said nothing died. Now a
   boot the watcher saw (`watch/boot-<id>`) that ended with an empty live
   set and no `cgr --save` record moves the window to the boot before it,
   repeatedly; a boot the watcher did not see stops the walk, because
   nothing says it was empty. Pinned by
   `test_an_empty_boot_after_the_crash_does_not_hide_it` (written first,
   failing with `{}`, as the real run did) and
   `test_a_boot_that_went_down_with_agents_ends_the_look_back`.
2. **Native sessions** from `claude()`, `codex()`, `omp()` and `hermes()`,
   keyed by (harness, id).
3. **Watcher records** from `watched()`, newest first, each passed through
   `resolve()` to find its session id in the harness's store:
   - claude: the id from its pid file, else the newest open transcript in its
     directory;
   - codex: the newest thread in its directory updated since it started;
   - omp: the newest unclosed log in its directory;
   - hermes: the newest open row in its directory.

   A `taken` set stops two dead processes from claiming the same session.
   Where a native item has the same id, the watcher record only adds the typed
   line, argv and start time.
4. **No id.** Harnesses without a readable store keep `id = None` and reopen
   with their own "continue the newest session here" flag. That flag can only
   reopen one session per directory, so only the newest such item per
   (harness, directory) is shown.
5. **Typed line.** From the watcher. Otherwise `typed_for()` runs against
   history now, which can miss (6.5).
6. **Hidden items.** An item is hidden when it is in the handled ledger
   (`cgr-handled.json`) with a time at or after its last activity, so it stays
   hidden until it dies again. It is also hidden when its id is on the command
   line of a running agent.

## 8. Rebuilding the launch command

`command()` turns a session into the line a new tab runs. The rule: **what you
typed, minus old session-picking flags and prompts, plus this harness's resume
flag.**

| typed | reopened as | rule |
|---|---|---|
| `claude` | `claude --resume <id>` | resume by id |
| `cc` (alias for a proxied claude) | `cc --resume <id>` | the alias runs as typed, with the resume flag appended |
| `codex -m gpt-6-sol` | `codex -m gpt-6-sol resume <id>` | flags kept; codex takes flags before the subcommand (measured) |
| `claude --resume old 'fix it'` | `claude --resume <new id>` | old session flag and its value dropped; the prompt dropped (an argument containing whitespace) |
| `cd ~/x && _proxy_up && BASE=http://127.0.0.1:8082 claude -c` | `_proxy_up && BASE=http://127.0.0.1:8082 claude --resume <id>` | `cd` segments dropped (the tab already starts there); proxy steps and variables kept |
| `ccc` (alias `claude --continue --fast`) | `ccc` | the alias picks its own session, so nothing is added |
| `oc-direct` (opencode, no id store) | `oc-direct --continue` | continue the newest session in that directory |
| a line with `$`, backticks, `\|`, `<` or `>` | the line as typed, then the resume flag | not parsed, to avoid breaking shell syntax |
| nothing in history | `/proc` argv, rebuilt the same way | fallback |

The table `HARNESSES` holds, for each of the 15 harnesses, the resume-by-id
arguments, the continue arguments, and the session flags to strip. They were
taken from each CLI's own `--help` on 2026-10-09:

| harness | resume by id | continue newest here |
|---|---|---|
| claude | `--resume <id>` | `--continue` |
| codex | `resume <id>` | `resume --last` |
| omp | `--resume <id>` | `--continue` |
| pi | `--session <id>` | `--continue` |
| hermes | `--resume <id>` | `--continue` |
| opencode | `--session <id>` | `--continue` |
| kilo | `--session <id>` | `--continue` |
| agy | `--conversation <id>` | `--continue` |
| jcode | `--resume <id>` | none: reruns the typed line |
| ante | `--resume <id>` | `--continue-session` |
| gemini | `--resume <id>` | `--resume latest` |
| qwen | `--resume <id>` | `--continue` |
| muse | `resume <id>` | `resume --last` |
| openclaw | none | `resume` |
| omx | `--resume <id>` | `--continue` |

Resume by id is used only where `cgr` reads a session id: claude, codex, omp
and hermes. For the others the id column documents the CLI, ready for when a
store reader is added (section 13).

**Why prompts are dropped.** A prompt typed on the launch line was already
sent once. Re-sending it into a resumed conversation would repeat the
instruction.

**Updated 2026-10-13 (T1).** `strip_args()` uses per-harness value-taking
options from the installed CLI help. It preserves option values, including
quoted spaces and multiple values where the CLI supports them, and drops
positional prompts of any length. Treating every word after a flag as a value
was rejected: a boolean such as `--yolo` does not own the following prompt.
Claude's single-dash `-resume` typo is removed too. Both the typed line and
the argv fallback use this rule, so repeated reopens cannot accumulate
`codex continue resume ...`. Environment prefixes, proxy steps and aliases
keep their existing behavior. Shell expressions remain an explicit limit.

## 9. Reopening

`reopen()` opens each chosen session in a new tab of the terminal `cgr` runs
in, trying these in order:

1. **Inside tmux** (`$TMUX`): a new tmux window.
2. **WezTerm** (`TERM_PROGRAM=WezTerm`): `wezterm cli spawn` into the focused
   window. On WSL `WEZTERM_PANE` does not cross into Linux, so
   `wezterm_window()` asks the mux which client has focus, and falls back to
   the only window there is. With `wezterm.exe` it passes
   `--domain-name WSL:<distro>` (measured: opens a tab in the WSL domain).
3. **Windows Terminal** (`WT_SESSION`): `wt.exe -w 0 new-tab ... wsl.exe -d
   <distro> --exec ...`. Semicolons are escaped because `wt.exe` splits its
   own command line on them.
4. **Otherwise**: a detached tmux session named `cgr`, attached at the end.

Each tab runs:

```
$SHELL -lic "cd DIR && { export CGR_LAUNCH=<typed>; COMMAND; }; exec $SHELL -l"
```

- `-i` loads the interactive config, so aliases and functions such as `cc` or
  `_proxy_up` exist. Measured: `zsh -lic` expands aliases inside the command
  string.
- `-l` gives the login environment (PATH, proxy settings).
- `exec $SHELL -l` leaves a working shell in the tab after the agent exits.
  Because `exec` keeps the pid and start time, the watcher's view of the
  terminal's shell stays valid.

If a tab cannot be opened, `cgr` prints the remaining commands to run by hand.
Only sessions that actually opened are marked handled.

## 10. Alternatives considered

Each row records what the option would have given, why it was not chosen, and
whether that judgement was **measured** here or only **reasoned**.

| # | alternative | what it offers | why not | basis | goals it fails |
|---|---|---|---|---|---|
| A1 | **`cg_run` launcher wrapper** (the status quo to 2026-08) | exact argv and environment for wrapped launches | sees only wrapped launches (12% of aliases); the SIGHUP trap recorded closed windows as clean; a missing stop record was read as a crash, giving 13,598 phantoms | measured (2026-08-20, 2026-10-09) | G1, G2, G3 |
| A2 | **mtime and marker forensics** (`cg-forensic`, `cg-recover`) | needs no daemon | an idle open session writes nothing, so mtime measures typing, not existence; graded precision 1.0 but recall 0.4 on the one boot with ground truth; on 2026-10-09 it missed codex and listed six unrelated sessions | measured | G1, G2 |
| A3 | **periodic full snapshots** (`cg-snapshot`, every 20s) | ground truth of what ran | ran as a bare process and died unnoticed (no snapshot since 2026-09-02); writes every process every scan (74 MB, 18,743 files); cannot separate a closed window from an exit | measured | G1 for closed windows, G7 |
| A4 | **zsh `preexec`/`precmd` hook** | the typed line and exit status of every command, exactly | runs on every command typed in every shell; zsh-only; must be sourced into each shell. Built on 2026-10-09 and removed the same evening at the user's direction | user decision | G4 |
| A5 | **per-harness hooks** (Claude `SessionEnd` and similar) | the harness's own word on how it ended | only some harnesses offer them; 15 separate configs to maintain; a hook only fires when the harness gets to run, so it never sees a machine death | reasoned; Claude behaviour measured | G4, G5 |
| A6 | **netlink process connector** (event-driven fork/exec/exit) | no polling at all | measured 2026-10-09: an unprivileged subscription works on this 6.18 kernel, but events carry pids from the WSL VM's outer pid namespace (21421, 21422, ...) that do not exist in this distro's `/proc`, and a child started during the test never appeared. Unusable inside WSL2 | measured | (WSL only) |
| A7 | **eBPF, auditd, fanotify** | precise process or exec tracing | eBPF needs privileges (`kernel.unprivileged_bpf_disabled = 2` here, measured); auditd must be configured as root; fanotify reports file access, not process lifetimes. All need root or system config changes | partly measured, partly reasoned | G4 |
| A8 | **`pidfd_open` + `poll`** | instant, unprivileged exit notification for known pids (Linux 5.3+) | does not discover new processes, so a `/proc` scan is still needed; exit timing finer than 2 s does not help recovery. Deferred, not rejected | reasoned | none |
| A9 | **terminal multiplexer persistence** (tmux, a WezTerm mux domain, tmux-resurrect) | prevents the closed-window case: processes outlive the window | every tab must be started inside the multiplexer, which changes the user's workflow; processes still die with the VM | reasoned | G3, G7; does not cover machine death |
| A10 | **shell history alone** | what was launched, and when | says nothing about whether it is still running or how it ended; `HIST_IGNORE_ALL_DUPS` deletes older launches (measured). Used for the typed line only | measured | G1, G2 |
| A11 | **codex thread writer locks** | per-thread liveness | the next codex start deletes stale locks (measured) | measured | G1 |
| A12 | **auto-restore at login** | zero keystrokes | a "window closed" may have been closed on purpose; opening it unasked could start the wrong conversation. `cgr --notify` prints one line instead, and `cgr` asks | reasoned | G2 |

**Why polling `/proc` every 2 seconds won.** It is the least invasive
mechanism that sees every harness and every terminal. It needs no privileges,
no shell or harness config, and no change in how anything is launched (G3, G4,
G5). It works inside WSL2, where the event-driven option (A6) does not. Its
cost is measured and small. The snapshotter's failure came from supervision
and from storing every process every scan, not from polling, so the watcher
keeps the polling and changes both of those: systemd supervises it, and it
only writes when the set of agents changes.

**Why the question is "did the shell die" and not "how did the agent
exit".** Exit codes and signals vary by harness and runtime: node, Rust and
Python handle SIGHUP differently, and some catch it and exit 0. The shell's
survival is set by the kernel's hangup semantics, so it means the same thing
for every harness (G5). It is also the one fact about a closed window that no
harness record carries (section 3).

**Why both sources are kept rather than only the watcher.** The native
records work for sessions the watcher never saw: before it was installed,
while it was down, or after it crashed. For claude they are definitive in all
three death modes. Keeping them means one missing component never blinds the
whole system.

## 11. Known limits and failure modes

### How the design meets the goals

| goal | mechanism | evidence | status |
|---|---|---|---|
| G1 cut off sessions found | machine death: native records plus the previous boot's live set; closed window: shell-liveness rule plus Claude's reason `other` | the 2026-10-09 crash listed exactly 9a3daff8 and 01a12388; the 2026-10-10 08:14 crash listed exactly the three sessions in the previous boot's live set; live window kills recorded for codex and opencode; tests `test_claude_stale_pidfile_is_machine_down`, `test_codex_thread_without_teardown_is_machine_down`, `test_watcher_records_an_agent_whose_window_closed`, `test_agents_alive_when_an_earlier_boot_ended_went_down_with_it` | verified |
| G2 nothing deliberate or unrelated | deliberate exits leave a live shell or a close record; window bounded at the previous boot; headless, exec and subagent runs excluded; running-again sessions hidden | Ctrl-C not recorded (live); `test_watcher_ignores_an_agent_you_exit`, `test_claude_deliberate_exit_is_not_listed`, `test_claude_headless_runs_are_not_listed`, `test_session_named_on_a_running_command_line_is_not_listed` | verified, with limits 1 and 2 below |
| G3 bare launches | no wrapper anywhere; `/proc` sees any launch | both crashed sessions were bare launches and were found | verified |
| G4 non-invasive | one background reader of `/proc`; no shell hook; the only harness hook is the pre-existing Claude `SessionEnd`, which fires once per session | `grep` of the installed shell file: 0 hook references; service cost measured | verified |
| G5 all harnesses | one detection rule for all; resume flags for 15 harnesses | opencode recorded through an alias (live); `HARNESSES` table from each `--help` | detection verified for codex, opencode, claude; others reasoned from the same rule |
| G6 launch arguments from history | `typed_for()` at launch, dated by `btime` read then; `command()` rebuild | `codex -m gpt-6-sol` and `oc-direct` captured live; six `test_command_*` tests; the clock-drift defect of 2026-10-10 and its two tests (6.5) | verified, with limits 7 and 8 |
| G7 simple | one command, a picker with everything ticked, Enter | `cgr --list` output checked against the 2026-10-09 crash. End to end on 2026-10-10: the VM died at 08:14:34; at 08:21:21 the user typed `cgr`, and at 08:21:33 it reopened all three sessions that were running (claude 65949efa, claude 9a3daff8, codex 01a1246e), each in its own directory, as the same conversation. One reopen carried a wrongly matched typed line (6.5) | verified |
| G8 own directory, same conversation, new tab | cwd from `/proc` or the store; resume by id for four harnesses, plus any harness whose id is on the command line (`argv_id()`); four tab openers | `wezterm.exe cli spawn` into the WSL domain and `zsh -lic` alias expansion measured by hand; the openers have no automated test | exact conversation for claude, codex, omp, hermes; newest-in-directory for the rest |
| G9 planned restart | `cgr --save` writes the running set with ids; `cgr` lists each once its process is gone (section 16) | 2026-10-10: four running sessions saved, each with its exact id (claude by pid file, codex by its command line, omp by its store); two tests, one against a real process in a pty | verified, not yet across a real restart |
| G10 database of every session | `cgr --history` reads 11 harnesses' stores into `sessions.db`, with status, evidence, mid-turn and last prompt (section 17) | 2026-10-10: 1,455 sessions, 383 flagged; mid-turn verdicts spot-checked against raw transcripts for claude, gemini, pi and qwen; three tests | verified |

### Limits, each with its consequence and the next step

1. **Exit, then close the window within one scan (about 2 to 4 s).** The
   agent is gone with the shell still alive, the record is held one scan, and
   by then the shell is dead too. It is recorded as "window closed": a false
   positive. You can untick it in the picker or run `cgr --forget`.
2. **Closing a window on purpose to end an agent** is listed. By design: it
   cannot be told apart from an accident, and the picker costs one keypress.
3. **Watcher not running.** Only native records remain: claude in every
   mode (for sessions that wrote a pid file, see 5.1), and machine death for
   codex, omp and hermes. Closed windows of other harnesses are missed. `cgr`
   prints a warning with the command to start the watcher.
4. **The agent is the session leader.** If it was started with `exec claude`,
   or a terminal was configured to run the agent directly, there is no shell
   to ask, so a closed window is not recorded. Machine death is still caught
   through the live set.
5. **No session store read for 11 harnesses.** opencode, kilo, agy, jcode,
   ante, gemini, qwen, muse, openclaw, omx and pi reopen with "continue the
   newest session in this directory". If a newer session exists there, the
   wrong one opens. Only one per (harness, directory) is offered. jcode has no
   continue flag, so it reruns the typed line and starts a new session.
6. **An alias that picks its own session** (for example `ccc` =
   `claude --continue`) runs as typed, so it reopens the newest session in the
   directory, not necessarily the one that died.
7. **Edge cases in rebuilding the command.**
   - An argument with whitespace is dropped as a prompt. If it was the value
     of a flag (`--system-prompt "be brief"`), the flag is left without its
     value.
   - A one-word prompt (`claude fixit`) is kept and sent again.
   - Lines with `$`, backticks, pipes or redirects get the resume flag
     appended at the very end, which is wrong for a pipeline.
   - Shell functions are not resolved, only `alias` lines in `~/.zshrc` and
     `~/.zsh_aliases`.
8. **History matching.**
   - zsh `EXTENDED_HISTORY` only; bash and fish users get the `/proc` argv
     fallback.
   - The 300-second window can miss a very slow alias.
   - Two launches of the same harness from two tabs within 5 seconds can swap
     typed lines.
   - A wrong match travels: the reopened tab carries it in `CGR_LAUNCH`, so
     every later reopen repeats it. That happened once, through the clock
     drift fixed on 2026-10-10 (section 6.5). A check that a direct launch's
     arguments match the process argv is on the backlog.
9. **Harness formats move.** Every native signal is a private implementation
   detail: Claude's sessions directory, codex's log strings and database
   names, omp's marker, the hermes schema. After a harness upgrade, re-run
   section 12.
10. **Codex machine-death detection depends on its logs database.** If codex
    prunes old log rows, older threads have no newest line and are skipped
    (missed, never falsely listed).
11. **Privacy of watcher state: fixed 2026-10-09.** `live-*.json` and
    `deaths.jsonl` hold argv, working directories and typed command lines.
    As first built they were created with the default umask (measured:
    directory `0775`, files `0664`), which broke the project's owner-only rule
    for `history.jsonl` (task T21, 2026-08-20). Now `private_dir()` makes the
    state directories `0700` and `write_private()` writes every file `0600`,
    and both repair older loose modes (verified on disk after a restart, and
    pinned by `test_state_is_owner_only_and_loose_files_are_repaired`). Explicit
    modes were chosen over `os.umask(0o077)` because a umask is inherited by
    the tabs `cgr` opens: a tmux server it starts would create every file in
    those sessions owner-only. What remains: the files are plain text, and
    anything running as the same user can read them.
12. **Single user, enforced 2026-10-10.** The watcher could only read the cwd
    and environment of its own account's processes, but it used to record
    other accounts' agents from their argv alone. Such a record, picked in
    `cgr`, would have run another account's flags as you. Now:
    - `mine()` keeps only this account's processes;
    - `read_own()` reads a state file only if this account owns it, so a
      planted record with a crafted typed line is ignored;
    - `clean()` shows control characters in titles and commands as `?`.

    Pinned by three tests, each written first and failing.
13. **WezTerm window choice** is a heuristic (the client with focus that was
    most recently active). With several windows and no focus data, a new
    window opens.
14. **Clock drift: fixed 2026-10-10** (sections 6.5 and 7). What remains:
    without the watcher, the boot's start is first read by the first `cgr`
    run of the boot, usually the login notice. If that comes hours late, the
    boundary carries the drift accumulated by then.
15. **`cgr --save` and `cgr --history`** have their own limits, listed in
    sections 16.4 and 17.7.

## 12. Audit procedure

Each check re-tests one claim above. Probes launch real harness processes,
and that has side effects, learned on 2026-10-09:

- a codex start deletes stale thread locks;
- commands typed into an interactive shell land in the shared zsh history;
- an interactive zsh started without a terminal (`zsh -ic` from a script)
  hangs, so run probes inside tmux.

Use a throwaway directory, and never probe while a real crashed session is
waiting to be restored.

```bash
# 0. The suite: 40 cgr tests (6 against real processes in a pty), 76 in all
cd /home/cheta/code/crash-guard && python3 -m pytest tests/test_cgr.py -q && python3 -m pytest tests -q

# 0b. Clock drift: btime now, the boot start cgr uses, and wtmp's stamp for this boot
python3 -c "import time; b=int([l for l in open('/proc/stat') if l.startswith('btime')][0].split()[1]); print('btime now ', time.ctime(b))"
f=$(ls ~/.local/state/crash-guard/watch/boot-$(cat /proc/sys/kernel/random/boot_id)); echo "boot_start  $(date -d @$(cat $f))"
last -x reboot -F | head -1

# 1. Watcher health, cost and recent errors
systemctl --user status cgr-watch --no-pager
journalctl --user -u cgr-watch --no-pager -n 20
ps -o pid,pcpu,rss,etimes,cmd -p "$(pgrep -f 'cgr --watch')"

# 2. Claude: the pid file disappears on SIGHUP, and SessionEnd says "other".
#    The pane's own pid is the probe, so no real session can be signalled.
#    The pid file may appear only after the first prompt: type one if step 2 shows nothing.
tmux new-session -d -s probe -c /tmp 'exec claude'; sleep 8
pid=$(tmux display -p -t probe '#{pane_pid}'); ls ~/.claude/sessions/$pid.json
kill -HUP $pid; sleep 5; ls ~/.claude/sessions/$pid.json   # expect: no such file
tail -1 ~/.claude/projects/-tmp/*.jsonl                    # expect: "reason": "other"

# 3. Codex: a teardown line is logged after /quit and about 60s after SIGHUP
python3 - <<'EOF'
import glob, os, re, sqlite3
db = max(glob.glob(os.path.expanduser("~/.codex/logs_*.sqlite")),
         key=lambda p: int(re.sub(r"\D", "", os.path.basename(p)) or 0))
con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
for row in con.execute("SELECT thread_id, datetime(MAX(ts),'unixepoch','localtime') FROM logs "
                       "WHERE feedback_log_body LIKE '%Agent loop exited%' "
                       "GROUP BY thread_id ORDER BY MAX(ts) DESC LIMIT 5"):
    print(row)
EOF

# 4. The shell-liveness rule, live: a closed window is recorded, Ctrl-C is not
tmux new-session -d -s probe2 -c /tmp 'zsh -l'; tmux send-keys -t probe2 'codex' Enter; sleep 10
tmux kill-session -t probe2; sleep 5
tail -1 ~/.local/state/crash-guard/watch/deaths.jsonl          # expect: a codex record, cwd /tmp
tmux new-session -d -s probe3 -c /tmp 'zsh -l'; tmux send-keys -t probe3 'codex' Enter; sleep 10
tmux send-keys -t probe3 C-c; sleep 1; tmux send-keys -t probe3 C-c; sleep 5
tail -1 ~/.local/state/crash-guard/watch/deaths.jsonl          # expect: no new record
tmux kill-session -t probe3
# Clean up the probe records only (never `cgr --forget`: it hides every listed session)
sed -i '/"cwd": "\/tmp"/d' ~/.local/state/crash-guard/watch/deaths.jsonl

# 5. Resume flags still match each CLI (repeat per harness in HARNESSES)
claude --help | grep -E -- '--resume|--continue'; codex --help | grep -E 'resume'

# 6. What cgr would do now, without doing it
cgr --list; cgr --json | python3 -m json.tool | head -40

# 7. cgr --save, without touching the real saved set: every running agent, each with an id
XDG_STATE_HOME=$(mktemp -d) cgr --save

# 8. The session database: rebuild it, then spot-check a mid-turn verdict by hand
cgr --history
sqlite3 -box ~/.local/state/crash-guard/sessions.db \
  "SELECT harness, status, count(*), sum(mid_turn) FROM sessions GROUP BY 1, 2"
p=$(sqlite3 ~/.local/state/crash-guard/sessions.db \
  "SELECT path FROM sessions WHERE harness='claude' AND mid_turn=1 LIMIT 1")
tail -3 "$p" | python3 -c "import sys,json; [print(json.loads(l).get('type'), str(json.loads(l).get('message',''))[:120]) for l in sys.stdin]"
```

When a check disagrees with this document, the code and the measurement win.
Record the disagreement and the fix in the Living Documents dossier
(`decisions.md`) and update this file in the same change.

## 13. Improvement backlog

In rough order of value against cost:

1. ~~Owner-only watcher state~~ (limit 11). Done 2026-10-09.
2. **Session id readers for more harnesses** (limit 5). The index (section
   17) now reads the stores of muse, opencode, kilo, pi, jcode, gemini and
   qwen. `resolve()` does not use them yet: matching a dead process to one of
   their sessions by directory and start time would turn "newest in
   directory" into an exact resume. agy still has no reader (its conversation
   id is the presence-lock filename). A session named on the command line is
   already resumed exactly, for every harness (`argv_id()`, 2026-10-10).
3. **Keep a flag's value when dropping prompts** (limit 7). Strip whitespace
   arguments only when they are positional.
4. **bash history** (`HISTTIMEFORMAT` timestamps) and fish history (limit 8).
5. **Check a matched line against the process** (limit 8). When the typed
   line starts the harness directly rather than through an alias, its
   arguments should equal the process argv. Reject the match when they do
   not, so that a wrong line is never carried forward in `CGR_LAUNCH`.
6. **Formal retirement of the August machinery.** Neither `cg-snapshot` nor
   `cg-forensic` is needed for recovery any more (section 15). Decide whether
   to delete them or keep them for reference.
7. **Turn state for muse** (17.7). Its log has turn-start events but no
   verified turn-end event, so `mid_turn` is left empty.
8. **A reviewed mark in `sessions.db`.** The audit view has no way to record
   "looked at, nothing left to do". A `reviewed` column set by a flag would
   let the list shrink as it is worked.
9. **Optional: `pidfd` exit notification** (A8) for exits faster than the
   2-second scan. Only worth it if something time-sensitive is added.
10. **Optional: the process connector on native Linux** (A6), where pids are
    in the same namespace. Not on WSL2.

## 14. Files and record formats

All state is under `~/.local/state/crash-guard/` (or `$XDG_STATE_HOME`).
Directories are `0700` and files `0600` (`private_dir()`,
`write_private()`), and `cgr` reads only files this account owns
(`read_own()`). Files are rewritten atomically (temp file, then
`os.replace`), except the append-only `deaths.jsonl`.

| path | written by | read by | content |
|---|---|---|---|
| `watch/live-<boot_id>.json` | watcher, on each change | `cgr` (other boots' files only) | JSON array of agent records running now |
| `watch/deaths.jsonl` | watcher | `cgr` | one agent record per line, plus `died` (epoch) |
| `watch/watch.pid` | watcher at start | `cgr` | two lines: pid, boot id |
| `watch/boot-<boot_id>` | first watcher or `cgr` run of the boot | `cgr`, watcher | this boot's start (epoch): `btime` as first read, before it drifts |
| `cgr-handled.json` | `cgr` after reopening or `--forget` | `cgr` | `{"<harness>:<id or cwd@started>": epoch}`; entries kept 30 days |
| `saved.json` | `cgr --save` (replaced by each save) | `cgr` | `{"saved": epoch, "sessions": [...]}`: `cgr --json` items plus `pid`, `ticks` and `boot` |
| `sessions.db` | `cgr --history`; the watcher once a day | you, with `sqlite3` | SQLite: table `sessions`, view `audit` (section 17.5) |

**Agent record** (watcher; a real record, the Claude session that wrote this
document):

```json
{
  "pid": 1408,
  "ticks": "14621",
  "started": 1791603515.21,
  "harness": "claude",
  "argv": ["claude"],
  "cwd": "/home/cheta/code/crash-guard",
  "leader": 378,
  "leader_ticks": "9160",
  "boot": "df80cb91-db7e-4334-a97d-6b4aae5783c3",
  "session": "65949efa-0d87-45b9-850e-8078441cd263",
  "typed": "claude"
}
```

`ticks` and `leader_ticks` are field 22 of `/proc/<pid>/stat` as strings (the
pid-reuse guard). `started` is `btime + ticks / CLK_TCK`, with `btime` read
when the watcher first sees the process, and kept across watcher restarts
(section 6.5). `leader` is the
terminal session id, which is the pid of the shell that owns the terminal.
`session` is filled for claude only (from its pid file). `typed` is
`CGR_LAUNCH`, else the history line, else null. Evolution rule, same as the
rest of the project: add fields, never repurpose one.

**`cgr --json` item:** `harness`, `id` (null when the harness's store is not
read), `cwd`, `last` (epoch of last activity), `how` (`machine went down`,
`killed`, `window closed`, `not closed`), `title` (the first prompt or the
typed line), `started`, `typed`, `argv`, `command` (the line the tab would
run).

## 15. Relationship to the rest of crash-guard

| component | status after 2026-10-09 |
|---|---|
| `bin/cgr`, `cgr-watch` | **the recovery path**: what to run after a crash or a closed window; also `--save` before a planned restart and `--history`, the database of every session |
| `bin/cg-session-end` | kept: gives claude the `window closed` reason; fires once at session end |
| `bin/crash-guard` (`restore`, `cg_run` ledger, `ledger` bridge) | unchanged and still works for the ledger and for `cg_run` launches; no longer the answer to "what died" |
| `bin/cg-recover`, `bin/cg-forensic` | superseded for recovery (A2); kept for reference and `crash-guard recover --compare` |
| `bin/cg-snapshot` | superseded by the watcher (A3); not running since 2026-09-02; its 74 MB store is the graded 2026-08-20 corpus |
| `shell/crash-guard.sh` | defines `cg_run`, `cgs`, `cgh`, `cgr-batch` (`cgr --yes`), and the once-per-boot `cgr --brief` line; no per-command hook |

The project's dated decisions, the evidence behind them, and the task state
live in the Living Documents dossier:
`/home/cheta/LIVING_DOCUMENTS/projects/crash-guard/` (start with
`start-here.md`; this design is summarized in `theory-of-operation.md`).

## 16. A planned restart: `cgr --save`

Added 2026-10-10 for goal G9.

### 16.1 What it does

`cgr --save` records every agent running on a terminal right now, each with
the id its harness gave it, in `saved.json`. You can then shut down however
you like: exit each agent, close the windows, `wsl --shutdown`, or reboot
Windows. The next `cgr` lists each saved session once its process is gone,
with how `saved`, and reopens the ones you pick as usual. Each `--save`
replaces the previous one. A saved session leaves the list once `cgr` has
reopened it or `cgr --forget` has hidden it (the same `cgr-handled.json` rule
as every other item).

```
$ cgr --save
cgr: saved 4 running sessions. After the restart, run cgr to reopen them.

  omp       ~/tmp                       10-10 12:01  saved              SPACE: Find unused space ...
      cd /home/cheta/tmp && omp --resume 01a12668-bc63-7316-a6ef-4528aad4c185
  claude    ~/code/crash-guard          10-10 12:01  saved              this completly failed ...
      cd /home/cheta/code/crash-guard && claude --resume 65949efa-0d87-45b9-850e-8078441cd263
  codex     ~/code/pareto               10-10 12:01  saved              continue
      cd /home/cheta/code/pareto && codex continue resume 01a1246e-87e1-71b1-9388-85f7ab17d02a
  ...
```

(Measured 2026-10-10 in a scratch state directory, so the real saved set was
not touched.)

### 16.2 How it works

`running_sessions()` builds a fresh `Watcher` and runs its `consider()` on
every process in `/proc`. That gives the same records the watcher keeps: only
this account's agents, only the outermost agent on each terminal, the typed
line kept from the watcher's live file when it has one. So `--save` works
whether or not the watcher is running. Each record is then resolved to a
session id:

- **claude**: its pid file names the session, following `/clear` and
  `/resume`. All running claude ids are reserved first, so a fresh claude with
  no transcript yet cannot take another running session's id.
- **any harness with an id on its command line** (`codex resume <id>`,
  `omp --resume <id>`): that id (`argv_id()`).
- **codex, omp, hermes otherwise**: the newest session in the process's
  directory touched since it started (`resolve()`, the crash path's lookup).
- **the rest**: no id; reopening uses the harness's "continue the newest
  session here" flag.

`saved()` hides a saved session while its process is alive in the same boot
(same pid and start ticks), so a save never lists anything while you are
still working.

### 16.3 Why this design

| option | why not, or why |
|---|---|
| rely on the watcher's live set alone | It already answers machine death and closed windows. But a restart where you exit each agent first looks like deliberate exits, which `cgr` correctly ignores. A save states intent, so it must be explicit. |
| a systemd `ExecStop` or shutdown hook that saves automatically | A WSL VM shutdown (`wsl --shutdown`, a Windows update) does not reliably stop user services in order, and a crash runs nothing. It would also save on every reboot, including ones you meant to start fresh from. |
| a terminal-multiplexer snapshot (tmux-resurrect style) | Only covers agents inside that multiplexer, and restores a process, not a conversation id. The user runs WezTerm and Windows Terminal tabs too. |
| **an explicit flag that saves ids and reuses the reopen path** | One command before, one after. Same picker, same rebuilt launch line, same tab opening. Nothing runs that would not run anyway. |

### 16.4 Limits

1. **Two sessions of one harness in one directory**, for codex, omp or hermes
   without an id on the command line: the newest-touched session is matched
   to the newest-started process, which can swap them.
2. **A fresh claude with no transcript** resolves to no id and reopens with
   `--continue`.
3. **Not yet verified across a real restart.** Verified: the save, the
   resolution of four live sessions to their exact ids, hiding while alive,
   listing once dead, and hiding after a reopen (tests
   `test_save_records_what_runs_and_lists_it_once_it_is_gone` against a real
   process in a pty, and `test_saved_sessions_from_the_boot_before_are_listed_by_id`).

## 17. Every session in one database: `cgr --history`

Added 2026-10-10 for goal G10.

### 17.1 What it does

`cgr --history` reads every session store on the machine, decides how each
session ended, and upserts one row per session into
`~/.local/state/crash-guard/sessions.db`. It prints a table per harness and
the sessions worth an audit grouped by date, newest first (the newest
dates holding 40 sessions, each date whole; `--all` prints every date,
`--json` every row; 18.4 and 18.5 describe the browser and its selectors). The watcher refreshes the database once a day in a low-priority
child process (`refresh_index()`), so sessions are captured before their
harness prunes them. Claude Code's default is 30 days; this machine sets
`cleanupPeriodDays` to 99999 (checked 2026-10-10), so the daily refresh is
a safeguard here, not a necessity.

First run, 2026-10-10: **1,455 sessions** from 11 harnesses, February to
October 2026, in 29 s cold and 10.6 s warm. **383** were flagged for audit;
263 automated runs are in the database but never flagged.

| harness | sessions | flagged | mostly because |
|---|---:|---:|---|
| hermes | 613 | 214 | `ended_at` never set (157), or hermes's own orphan repair |
| omp | 129 | 45 | no `session_exit`, or one naming SIGHUP/SIGTERM |
| opencode | 154 | 38 | stops mid-turn, no end record kept |
| pi | 126 | 29 | stops mid-turn |
| qwen | 50 | 28 | stops mid-turn |
| claude | 59 | 8 | pid file left, reason `other`, or no `SessionEnd` record |
| muse | 37 | 6 | no `session.end`, or `exit_reason` `incomplete` |
| gemini | 43 | 5 | stops mid-turn |
| codex | 137 | 4 | no shutdown line in its log, or mid-turn |
| openclaw | 55 | 3 | window status `failed` |
| jcode | 52 | 3 | status `Crashed` |

### 17.2 Where each harness's sessions come from

| harness | store | end record | turn state |
|---|---|---|---|
| claude | `~/.claude/projects/*/<id>.jsonl` | `session_exit` from `cg-session-end` (`SessionEnd`); a pid file left behind | last message: tool call, tool result, prompt, or reply |
| codex | `state_*.sqlite` `threads`, plus the rollout file | `Agent loop exited` or `op.dispatch.shutdown` in `logs_*.sqlite` (covers about 10 days) | `task_started` with no `task_complete` or `turn_aborted` after it |
| omp, pi | `~/.omp/agent/sessions`, `~/.pi/agent/sessions` | omp: `session_exit` with its reason; pi writes none | last message, including `stopReason` `aborted` and `error` |
| hermes | `~/.hermes/state.db` `sessions` and `messages` | `ended_at`, `end_reason` | last message; `Operation interrupted` counts as stopped |
| jcode | `~/.jcode/sessions/*.json` | `status`: `Closed`, or `Crashed` with a message | last message |
| muse | `~/.local/share/muse/sessions/Y/M/D/<id>/session.jsonl` | `session.end` with `exit_reason` | not read (17.7) |
| gemini | `~/.gemini/tmp/<project>/chats/*.json`, cwd from `.project_root` | none | last message; tool calls with no text count as a call |
| qwen | `~/.qwen/projects/*/chats/*.jsonl` | none | last message (`functionCall`, `functionResponse`) |
| opencode, kilo | `opencode.db` / `kilo.db` `session`, `message`, `part` | none (`time_archived` counts as closed) | last message: no `time.completed`, `finish` `tool-calls`, or `error` |
| openclaw | `agents/*/agent/openclaw-agent.sqlite` `session_windows` | `status` `done` or `failed` | not kept |

Each store was opened and sampled on 2026-10-10 before its reader was
written. kilo has no store on this machine yet, and ante and agy keep none
that `cgr` can read.

### 17.3 How a status is decided

Each row gets one status. The first rule that applies wins:

1. **running**: a live process holds it (`running_sessions()`, Claude's live
   pid files, or its id on a running command line).
2. **cut off**: the crash path found it (`cut_off(everything=True)`): a
   watcher death or live-set record, a stale Claude pid file, and so on. Its
   `how` is the crash path's: `machine went down`, `window closed`, `killed`,
   `not closed`, and `saved`. A `--save` record whose process is gone is
   evidence (the planned restart): it keeps `how` = `saved`, through
   `merge()` and into this table, unless the harness wrote a deliberate exit
   record (rule 3 then wins). It still does not mint rows without an id.
3. **the harness's own end record**: closed (`exit record: dispose`,
   `status Closed`, `end record: cli_close`) or cut off (`CUT_EXITS`, jcode
   `Crashed`, hermes `HERMES_CUT`, muse `exit_reason` other than `clean`,
   codex with log lines but no shutdown, openclaw `failed`).
4. **no end record where one is always written**: for claude (interactive
   only), omp and muse (`WRITES_EXIT`), a session with no end record that is
   newer than the first one seen in that store never got to exit: **cut off**,
   `no end record`. Older ones predate the record and stay unknown.
5. **unknown**: no evidence either way.

**Mid-turn** is separate from status: the record stops on your prompt, a tool
call, a tool result, or an error, so the agent was still working. A deliberate
stop (`[Request interrupted by user]`, omp `aborted`, codex `turn_aborted`)
ends the turn. `turn()` reads conversation steps newest first and takes the
first one; `step()` maps every harness's message shape onto the same six
kinds.

**Flagged for audit** (`flagged()`, the `audit` view): sessions you ran
(`interactive` = 1) that are cut off, or unknown and mid-turn. A harness's
clean-exit record outranks mid-turn: hermes ends a session mid-turn on purpose
when it compresses it into a continuation. Automated runs (`AUTOMATED`: SDK,
`exec`, `mcp`, subagents, cron, kanban, gateway) are kept with
`interactive` = 0.

**Measured, not assumed.** The mid-turn verdict was checked by hand against
the raw transcript for one flagged session each of claude (last record a
`tool_use`), gemini (tool calls with no text), pi (a prompt answered by an
`error`) and qwen (a prompt with no answer). hermes's end reasons were read
from its source: `startup_orphan_reap` and `ws_orphan_reap` close rows whose
process died, and `orphaned_compression` closes a continuation that was never
used or closed.

### 17.4 Persistence

Rows are upserted, never deleted. A session its harness has since deleted
keeps its row with `in_store` = 0, so the database becomes the longer
history. A `cut off` verdict is sticky: if the evidence for it is pruned
later (the watcher keeps 30 days), the row does not fall back to `unknown`.
`typed` and `reopened` keep their last known value. A store that fails to
read is reported and its rows are not marked gone. Columns added by a later
`cgr` are added to an older database in place.

### 17.5 Using it

```bash
cgr --history                      # refresh, summary, flagged sessions by date (newest ~40)
cgr --history --all                # every flagged date
cgr --history --list --group 2     # one date by its printed number; --day YYYY-MM-DD by name
sqlite3 -box ~/.local/state/crash-guard/sessions.db 'SELECT * FROM audit'
sqlite3 -box ~/.local/state/crash-guard/sessions.db \
  "SELECT harness, id, cwd, datetime(last,'unixepoch','localtime'), how, last_prompt
   FROM sessions WHERE cwd LIKE '%/pareto%' ORDER BY last DESC"
```

Columns: `harness`, `id`, `cwd`, `source`, `interactive`, `started`, `last`
(epoch seconds), `status`, `how`, `exit_record` (verbatim), `mid_turn`,
`title`, `last_prompt` (both cut to 400 characters), `messages` (where the
harness counts them: hermes, opencode, jcode, gemini), `bytes` (transcript
size, where the session is a file), `model`, `branch`, `path`, `typed`,
`argv` (the process argv as JSON, when the watcher saw it), `resume` (the line that reopens it), `reopened`, `in_store`, `first_seen`,
`last_seen`. The `audit` view adds `last_active` in local time.

### 17.6 Why this design

| option | why not, or why |
|---|---|
| cass (`cass search`) as the inventory | cass indexes most of these harnesses, but it is a separate archive with its own staleness (`cass-reindex`), and it records content, not how a session ended. The end-state signals live in each harness's own store, so they have to be read there anyway. |
| file modification times only | An idle session writes nothing, and a clean exit and a crash look the same. That is what failed on 2026-10-09 (section 2). |
| a JSON or CSV file | The user asked for a database, and the audit is a query: by folder, by harness, by date, by status. SQLite is in Python's standard library and `sqlite3` is on the machine. |
| rebuild from scratch each run | Loses sessions their harness has deleted. Upsert with `in_store` keeps them. |
| count every transcript line for a message count | Measured: 7 s just to read 1.27 GB of JSONL, for a number that means records, not messages. `bytes` gives the size cheaply; `messages` is filled only where the harness stores a real count. |
| **one reader per store, one status rule, one table** | Each reader is about 20 lines and reads only the head and tail of a transcript. One precedence rule (17.3) decides every status, so a verdict can be traced to its evidence in `how`. |

### 17.7 Limits

1. **pi, gemini, qwen and opencode record no end.** Their sessions are
   `unknown` unless the watcher saw them die, and only mid-turn ones are
   flagged. A session idle at the prompt when its window closed, before the
   watcher existed, is not flagged.
2. **opencode and qwen headless runs look interactive.** `opencode run` and
   probe prompts ("Reply with exactly: OK") are stored like TUI sessions, so
   some flagged rows are scripted probes.
3. **codex's log covers about 10 days** (`logs_2.sqlite` from 2026-09-30).
   Older threads are `unknown`, except mid-turn ones from the rollout.
4. **claude sessions before 2026-09-04**, when `cg-session-end` was installed,
   have no end record; they are `unknown` unless a pid file or the watcher
   says otherwise.
5. **muse turn state is not read**: its log has turn-start events, but no
   turn-end event was verified.
6. **openclaw has no working directory** (it runs as a gateway); `cwd` is
   empty and `resume` is only `openclaw resume`.
7. **Prompts are stored.** `title` and `last_prompt` hold the first and last
   prompts. The database is `0600` in the owner-only state directory, like the
   rest of `cgr`'s state.
8. **Formats move.** Each reader depends on a private store layout. A reader
   that fails prints `cgr: <harness>: <error>` and the rest still run; re-run
   audit check 8 after a harness upgrade.


## 18. Recovery corrections, 2026-10-13

T1 and T2 follow the handoff based on `496b4df`. Tests were added before
behavior changes. The baseline was 78 tests; T1 passes 82, and T2 passes 93
plus 31 subtests. No agent tabs were opened, and no commit or push was made.

| defect | impact and evidence | fix and regression |
|---|---|---|
| Single-word prompts and `-resume` survive reconstruction | `codex continue resume T` and `claude -resume --resume X` repeat on later reopens | Per-harness option values; four `test_command_*` regressions in T1 |
| Global subcommands and `-p` filter | `omp models` was offered as a dead session; Codex profiles and jcode providers were excluded as headless | CLI-specific command and flag sets; `test_harness_subcommands_and_one_shot_flags_are_specific_to_each_cli` and `test_old_watcher_one_shots_are_not_recoverable` |
| Live-file mtime dates the set's last change | An idle agy appeared to die hours before shutdown, and a handled time could hide a later death | `boot_periods()` pairs wtmp shutdowns inside each interval, else uses the next boot; watcher evidence updates native event time; the three boot-date and merge regressions |
| agy has no resolved id | `--continue` could select a conversation created after the crash | A unique presence lock within 60 seconds of launch, verified against `trajectory_meta.cascade_id`; two agy resolution tests |
| Missing typed lines borrow unrelated history | A cgr reopen without an inherited line could borrow an earlier launch's model | `launch_matches()` compares options against process argv; generated tabs always carry `CGR_LAUNCH`, using the reconstructed command when history is absent; two history/environment tests |
| Short resume flags ignored; store lookup runs beyond death | The newest transcript could be from a later session | `argv_id()` accepts CLI-specific short forms; unresolved native lookups stop at death plus five seconds; `test_resolve_uses_short_resume_flags_and_ignores_future_transcripts` |
| WezTerm title timeout after successful spawn | The opened session was not marked handled and could be opened twice | Title failure is reported without losing the spawn; `test_a_tab_title_failure_does_not_lose_a_successful_spawn` uses mocks, never launches |

CLI help was read for all 13 installed harnesses. ante and omx are absent;
their prior definitions remain unverified. Interactive attach/resume forms
remain eligible. One-shot records already on disk are filtered on read, so
correcting the watcher does not require deleting its historical evidence.

The agy process began at 2026-10-12 18:41:16. Its only matching lock is
`4f946590-7669-46a1-8344-b2c15a7d44a5.lock`, dated 18:41:32. The matching
conversation database has that cascade id; later locks are excluded. This is
an inferred launch association supported by a unique timestamp match, not a
pid-to-id field: the lock is empty. Ambiguous matches retain no id.

The six real watcher records now all carry the wtmp shutdown time
2026-10-13 05:05:30. The journal's power-off request at 05:05:24 precedes
that accounting record by six seconds. A crash without a shutdown record
has an inferred end, labeled as such. Default recovery still excludes boots
before the selected crash boot; changing event dates must not widen that
window.


### 18.1 Event grouping (T3, 2026-10-13)

`collect()` attaches a stable `group` key and `event` metadata, preserving
the JSON array interface. Watcher boot ids are authoritative associations;
native-only sessions use the interval containing their last activity and
say `event_association: last activity`. Window closes form their own group
within each boot. Sessions outside known intervals use daily activity
groups without inventing a crash. Groups sort by end time, newest first;
sessions within a group sort by their event time.

`--all` includes older groups. Text and the picker collapse groups older than
seven days; `--expand-old` or the picker's `o` key expands them. Old groups
start unselected to avoid reopening a historical archive merely by pressing
Enter. Space on a header toggles the whole group; individual rows can still
be selected. `a` selects or clears all groups explicitly. JSON remains
complete. `--yes --all` retains its explicit all-session behavior.

Five regressions cover two crash boots plus a window close, JSON shape and
keys, collapse/expand behavior, bulk group selection, and arrow-key movement
between headers. Picker tests use a pty and return a selection without
calling any opener. Full suite: 98 passed and 31 subtests passed.


### 18.2 Indexed recovery and harness coverage (T4, 2026-10-13)

`indexed_cut_off()` adds harnesses outside the four native recovery readers
from `sessions.db`: interactive, cut-off rows with `in_store = 1`, a real
session id and an available recorded path when one exists. Unknown, automated,
synthetic-id and missing-store rows stay out. Default recovery applies the
same look-back floor; the existing (harness, id) key merges watcher evidence
with the row. A current-boot missing end marker requires watcher evidence,
because it can be an idle live session rather than a death.

`ensure_index()` refreshes missing, day-old or older-schema databases.
`build_index()` calls `cut_off(use_index=False)`, so verdicts always come
from primary stores and watcher evidence. A fresh index is reused. Additional
native readers also resolve unnamed watcher processes by directory and
activity between their launch and death. Indexed rows do not borrow launch
flags from history using their original session creation time. A watcher
resolved id is retained in history even if that harness has no native indexer,
as for agy; its old synthetic row is retained as unavailable history.

OpenClaw's installed `resume` command searches recent session ids as well as
keys, labels and names. The exact id is now passed, instead of opening a
picker for an unspecified recent session. Its installed implementation is
`tui-session-picker-DNnzU8Pp.mjs`, `buildSessionChoices()` and
`resolveResumeSession()`. This still depends on Gateway discovery exposing
the historical window; reopening each old OpenClaw conversation was not
performed. No CLI or provider configuration was changed.

Nine T4 tests cover indexed jcode/muse recovery, row eligibility, default
windows and watcher merging, live-current-boot protection, a single stale
refresh without recursion, fresh-index reuse, native jcode id resolution,
OpenClaw's named query and agy's resolved id in history. Full suite: 107
passed and 31 subtests passed. A live `--history --json` refresh exited zero
with no stderr. `--all --json` lists 147 sessions in 69 groups across agy,
claude, codex, hermes, jcode, muse, omp and openclaw; ordinary recovery still
lists exactly the six from the handoff.

The zero-cut-off explanation is a capability limit, not evidence of clean
shutdown: pi 126, gemini 43, qwen 50 and opencode 154 are all `unknown`, and
none has an exit record. Their mid-turn counts are respectively 29, 5, 28
and 38. `exit_verdict()` can read an explicit marker when present, but
`WRITES_EXIT` contains only claude, omp and muse, so missing markers in
those four stores cannot be promoted to cut-off evidence. The watcher can
supply positive death evidence for any of them.


### 18.3 Final verification corrections and recovery gate (T5, 2026-10-13)

The final review added five regressions to the T2 audit:

- Installed pi and qwen entrypoints have generic filenames, so program-name
  detection missed them. Only their recognized package paths now identify
  `cli.js` and `cli-entry.js`; unrelated scripts stay excluded.
- Initial prompts owned by `-i`, `--prompt-interactive` and `--prompt` must
  lose their flag too. Otherwise the new resume flag can become the old
  prompt's value, or the prompt can be replayed.
- Qwen's positional query is one-shot unless interactive mode is requested,
  according to the installed CLI help. Such a process is excluded.
- One-shot records in old live and saved files must not stop the empty-boot
  walk. The same classification applies to boot selection and save recovery.
- Two historical jcode directories and one hermes directory no longer exist.
  `reopen()` reports an unavailable directory without spawning or marking
  the session handled. The regression mocks every opener and launches no tab.

The final suite is 112 tests and 35 subtests, all passing both in the working
checkout and in a clean tracked snapshot of `496b4df` with the five-file diff
applied. `git diff --check` passes. The watcher was restarted at 18:31 PDT
and read back active, running, with `ExecMainStatus=0`.

The read-only six-session gate passed: exact harness/id set, well-formed
commands, two Claude transcripts, Codex's thread and rollout, two OMP logs,
agy's cascade id, six existing folders, and a reachable WezTerm CLI with a
resolved window. All six belong to the 2026-10-13 05:05:30 shutdown group.
No tabs were opened. Conversation loading remains the operator's check:
run `cgr`, select all six, and confirm each tab shows the intended conversation.

The detailed result and file-level diff are in the crash-guard dossier's
`handoff-2026-10-13-codex-result` page. Source edits remain uncommitted and
unpushed; HEAD and origin/main stay at `496b4df`.

### 18.4 Date-grouped history browser and saved-row dedupe (T6, 2026-10-17)

`cgr --history` without a tty flag now browses one line per date with its
session count, colored like the picker (green saved, yellow window, red
crash). Space opens a date into its sessions, each with its reopen command;
space ticks sessions, `a` ticks the open date, Enter reopens the ticked set
through `reopen_history()`, which maps index rows back to session dicts and
reuses the tab opener, `mark_handled()` and unavailable-directory reporting.
`--list` prints the same date groups without opening tabs; `--all` shows
every date instead of capping at the newest 40. JSON shape is unchanged.
`@`-prefixed rows stay out of the pick list (18.5 widens this to every
harness): they name no session and cannot be reopened by id.

Two recovery defects found live on 2026-10-17 (four saved omp windows each
listed twice, as `saved` plus an id-less `window closed` twin):

- Live omp writes the signal nested (`{"customType": "session_exit",
  "data": {"reason": "sighup"}}`); `exit_reason()` read only the flat
  `reason`, so the saved transcript was invisible to native detection and to
  watcher `resolve()`. It now reads both, matching `tail_scan()`.
- `merge()` lets a `saved` row absorb the watcher's id-less row for its
  window (same harness, cwd, start within five minutes), rekeyed under the
  real id. Without the absorb step the two keys never meet and both list.

Three regressions: nested-sighup detection and resolution, the saved-absorbs-
watcher merge, and date grouping plus `reopen_history()` command building.
Full suite: 120 passed and 35 subtests passed.

### 18.5 Requested-versus-shipped audit fixes (T7, 2026-10-18)

An audit of 184be92 against the three requests (show all sessions; group by
date with a count; select, then pick sessions "like the first screen") found
`--save` green dead on real data. Fixes, each pinned by a test written first:

- **`saved` survives the restart.** The watcher row for a saved window resolved
  to the same id and won the merge (the saved row's `last` is the earlier save
  time), and `group_sessions()` only kept `kind="saved"` for an open boot. Now
  `merge()` pins `how="saved"` whichever row arrives last (and keeps the later
  `last`), and `group_sessions()` keeps `saved` on a closed boot period, so the
  row is green in `--list`, `--brief`, the picker and history.
- **History keeps saved evidence** (`build_index()` no longer drops it), so
  `sessions.db` holds `how="saved"`. A harness's deliberate exit record still
  outranks it. This corrects the 17.3 rule 2 wording and the handoff claim
  "8 rows, correct hows" (ids merged; the hows had been demoted).
- **Date labels.** Color follows the date's dominant kind (a tie takes the
  worse) and a mixed date prints its counts, instead of one crash painting a
  mostly-saved date red.
- **No partial dates.** The 40-row cap now ends on a date boundary and the
  header says `showing N of M`.
- **Selection without a terminal.** `--group N` (dates as printed) and `--day
  YYYY-MM-DD` filter `--history`; `--history --yes` reopens a selected date
  and is still refused with none. A selected or `--expand-old` date prints
  expanded; dates older than seven days otherwise print collapsed, as in the
  recovery list.
- **Preview parity.** `--preview` adds the id/messages/model/last-prompt line
  under each session in the list and the picker. The picker also stopped
  cutting an entry's second line (the reopen command) off at the terminal
  width, and counts screen lines, not entries.
- **`reopen_history()`** carries `started`, `messages`, `bytes`, `model`,
  `mid_turn` and `last_prompt`, and a new `argv` column (JSON) rebuilds the
  launch flags when `typed` is unknown. An id-less row is refused with a
  message instead of reopened with `--continue`; the history view hides
  those for every harness and counts them. `write_index()` purges stale
  native `@` rows left by older databases.
- **`merge()` absorb** takes the nearest start among candidates (still under
  300 s), and refuses a pair whose `pid`/`ticks` or typed lines are both known
  and differ. Resolved watcher rows now carry `pid` and `ticks` (added JSON
  keys; none removed).
- **`resolve()` for omp** ranks transcripts by how near their header start is
  to the process start, so two bare windows in one directory pair correctly
  whichever resolves first (touched-last remains the fallback).
- **`session_exit_reason(rec)`** is the one place the exit rule lives, read by
  `exit_reason()` and `tail_scan()`, including a string-shaped `data`.

Remaining limit: id-less watcher rows of store harnesses are still minted
into `sessions.db` (they back `--json` and the recovery list), only hidden
from the history picks.
