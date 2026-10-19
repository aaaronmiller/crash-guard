"""cgr finds sessions from the harnesses' own records and from its process
watcher, and reopens them with the command you typed -- one test per rule."""
import importlib.machinery
import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import pty
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_cgr():
    loader = importlib.machinery.SourceFileLoader("cgr", str(ROOT / "bin" / "cgr"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def jsonl(path, records, when):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    os.utime(path, (when, when))


def prompt(text, entry="cli", cwd="/w"):
    return {"type": "user", "entrypoint": entry, "cwd": cwd,
            "message": {"role": "user", "content": text}}


REPLY = {"type": "assistant", "message": {"role": "assistant", "content": "ok"}}


def exit_marker(reason):
    return {"type": "custom", "customType": "session_exit", "reason": reason}


class CgrTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cg = load_cgr()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.saved = (self.cg.HOME, self.cg.STATE, self.cg.WTMP, os.environ.get("HISTFILE"))
        self.cg.HOME = self.home
        self.cg.STATE = self.home / "state"
        self.cg.WTMP = self.home / "wtmp"
        os.environ["HISTFILE"] = str(self.home / ".zsh_history")
        self.cg.HARNESSES["cgrtestagent"] = (None, None, set())
        self.btime, self.boot_id = self.cg.current_boot()
        self.prev = self.btime - 3 * 3600          # the boot that died
        self.crash = self.btime - 600              # last sign of life in it
        self.write_wtmp([self.prev - 86400, self.prev, self.btime + 90])

    def tearDown(self):
        self.cg.HOME, self.cg.STATE, self.cg.WTMP, hist = self.saved
        self.cg.HARNESSES.pop("cgrtestagent", None)
        if hist is None:
            os.environ.pop("HISTFILE", None)
        else:
            os.environ["HISTFILE"] = hist
        self.tmp.cleanup()

    def write_wtmp(self, boots):
        rec = self.cg.UTMP
        blob = b"".join(rec.pack(self.cg.BOOT_TIME, 0, b"~", b"~~", b"reboot", b"", 0, 0, 0,
                                 int(t), 0, b"", b"") for t in boots)
        blob += rec.pack(7, 1, b"pts/0", b"", b"u", b"", 0, 0, 0, int(self.prev) + 5, 0, b"", b"")
        self.cg.WTMP.write_bytes(blob)

    def pidfile(self, pid, sid, cwd, started, ticks, entry="cli"):
        d = self.home / ".claude" / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        (d / ("%s.json" % pid)).write_text(json.dumps({
            "pid": pid, "sessionId": sid, "cwd": cwd, "startedAt": started * 1000,
            "procStart": str(ticks), "entrypoint": entry}))

    def transcript(self, sid, records, when, slug="-w"):
        jsonl(self.home / ".claude" / "projects" / slug / (sid + ".jsonl"), records, when)

    def history(self, entries):
        (self.home / ".zsh_history").write_text(
            "".join(": %d:0;%s\n" % (int(t), line) for t, line in entries))

    def zshrc(self, text):
        (self.home / ".zshrc").write_text(text)

    def found(self, everything=False):
        return {(s["harness"], s["id"]): s for s in self.cg.collect(everything)}

    # -- boots ---------------------------------------------------------------

    def test_previous_boot_comes_from_wtmp_and_skips_this_boot(self):
        self.assertEqual(self.cg.previous_boot_start(self.btime), self.prev)

    def test_previous_boot_without_wtmp_falls_back_a_day(self):
        self.cg.WTMP = self.home / "missing"
        self.assertEqual(self.cg.previous_boot_start(self.btime), self.btime - 86400)

    def test_a_late_reading_of_btime_does_not_move_the_boot(self):
        # btime is the wall clock minus uptime, and in WSL the two drift: 12 hours
        # into a boot it read 18 minutes before the boot's own wtmp stamp
        # (2026-10-10). Read that late, a codex thread busy in the last minutes
        # before the crash looked like this boot's work and was dropped.
        t = int(self.btime - 300)
        self.codex_db([("late", "/p", "x", t, t - 900, "cli", "user")],
                      [(t, "op.dispatch.turn_input", "late")])
        self.cg.boot_start()  # the watcher reads it as the boot starts
        real = self.cg.current_boot
        self.cg.current_boot = lambda: (real()[0] - 1200, real()[1])
        try:
            self.assertIn(("codex", "late"), self.found())
        finally:
            self.cg.current_boot = real

    # -- claude --------------------------------------------------------------

    def test_claude_stale_pidfile_is_machine_down(self):
        self.pidfile(999999, "dead", "/w", self.prev + 60, 1)
        self.transcript("dead", [prompt("fix the thing"), REPLY], self.crash)
        s = self.found()[("claude", "dead")]
        self.assertEqual((s["how"], s["cwd"]), ("machine went down", "/w"))
        self.assertEqual(self.cg.command(s), "claude --resume dead")

    def test_claude_sighup_exit_is_window_closed_even_without_pidfile(self):
        self.transcript("hup", [prompt("hi"), REPLY, exit_marker("other")], time.time() - 60)
        self.assertEqual(self.found()[("claude", "hup")]["how"], "window closed")

    def test_claude_deliberate_exit_is_not_listed(self):
        self.transcript("quit", [prompt("hi"), REPLY, exit_marker("prompt_input_exit")],
                        self.crash)
        self.pidfile(999998, "quit", "/w", self.prev + 60, 1)
        self.assertNotIn(("claude", "quit"), self.found())

    def test_claude_headless_runs_are_not_listed(self):
        self.transcript("sdk", [prompt("review", entry="sdk-py"), REPLY, exit_marker("other")],
                        self.crash)
        self.pidfile(999997, "sdk2", "/w", self.prev + 60, 1, entry="sdk-cli")
        self.transcript("sdk2", [prompt("review", entry="sdk-cli"), REPLY], self.crash)
        found = self.found()
        self.assertNotIn(("claude", "sdk"), found)
        self.assertNotIn(("claude", "sdk2"), found)

    def test_claude_session_resumed_and_alive_is_not_listed(self):
        me = os.getpid()
        self.pidfile(me, "back", "/w", time.time() - 30, self.cg.proc_ticks(me))
        self.pidfile(999996, "back", "/w", self.prev + 60, 1)
        self.transcript("back", [prompt("hi"), REPLY, exit_marker("other")], self.crash)
        self.assertNotIn(("claude", "back"), self.found())

    def test_session_named_on_a_running_command_line_is_not_listed(self):
        # A resumed claude writes its pid file late, but its argv names the session.
        self.pidfile(999993, "again", "/w", self.prev + 60, 1)
        self.transcript("again", [prompt("hi"), REPLY], self.crash)
        self.assertIn(("claude", "again"), self.found())
        script = self.home / "claude.py"
        script.write_text("import time\ntime.sleep(30)\n")
        # Its own session, so no terminal: the real cgr-watch never records it.
        p = subprocess.Popen([sys.executable, str(script), "--resume", "again"],
                             start_new_session=True)
        self.addCleanup(lambda: (p.kill(), p.wait()))
        for _ in range(100):
            if self.cg.proc_argv(p.pid)[-1:] == ["again"]:
                break
            time.sleep(0.05)
        self.assertNotIn(("claude", "again"), self.found())

    def test_claude_older_boots_need_all(self):
        self.pidfile(999995, "old", "/w", self.prev - 86400 + 60, 1)
        self.transcript("old", [prompt("hi"), REPLY], self.prev - 3600)
        self.assertNotIn(("claude", "old"), self.found())
        self.assertIn(("claude", "old"), self.found(everything=True))

    def test_native_session_picks_up_the_typed_launch_line_from_history(self):
        started = self.prev + 60
        self.history([(started - 400, "claude --model haiku"), (started - 3, "claude --model opus"),
                      (started - 1, "ls")])
        self.pidfile(999994, "typed", "/w", started, 1)
        self.transcript("typed", [prompt("hi"), REPLY], self.crash)
        s = self.found()[("claude", "typed")]
        self.assertEqual(self.cg.command(s), "claude --model opus --resume typed")

    # -- codex ---------------------------------------------------------------

    def codex_db(self, threads, logs):
        d = self.home / ".codex"
        d.mkdir(parents=True, exist_ok=True)
        st = sqlite3.connect(d / "state_5.sqlite")
        st.execute("CREATE TABLE threads (id TEXT, cwd TEXT, title TEXT, updated_at INTEGER, "
                   "created_at INTEGER, source TEXT, thread_source TEXT, archived INTEGER)")
        st.executemany("INSERT INTO threads VALUES (?,?,?,?,?,?,?,0)", threads)
        st.commit()
        st.close()
        lg = sqlite3.connect(d / "logs_2.sqlite")
        lg.execute("CREATE TABLE logs (ts INTEGER, feedback_log_body TEXT, thread_id TEXT)")
        lg.executemany("INSERT INTO logs VALUES (?,?,?)", logs)
        lg.commit()
        lg.close()

    def test_codex_thread_without_teardown_is_machine_down(self):
        t = int(self.crash)
        self.codex_db(
            [("cut", "/p", "build it", t, t - 900, "cli", "user"),
             ("quit", "/p", "x", t, t - 900, "cli", "user"),
             ("exec", "/p", "x", t, t - 900, "exec", "user"),
             ("sub", "/p", "x", t, t - 900, '{"subagent":{}}', "subagent")],
            [(t, "op.dispatch.turn_input", "cut"),
             (t - 100, "op.dispatch.turn_input", "quit"), (t, "x: Agent loop exited", "quit"),
             (t, "op.dispatch.turn_input", "exec"), (t, "op.dispatch.turn_input", "sub")])
        found = self.found()
        self.assertEqual(self.cg.command(found[("codex", "cut")]), "codex resume cut")
        self.assertEqual(found[("codex", "cut")]["cwd"], "/p")
        for tid in ("quit", "exec", "sub"):
            self.assertNotIn(("codex", tid), found)

    def test_codex_thread_reopened_after_a_teardown_and_cut_off_is_listed(self):
        t = int(self.crash)
        self.codex_db([("again", "/p", "x", t, t - 9000, "cli", "user")],
                      [(t - 3000, "Agent loop exited", "again"),
                       (t, "op.dispatch.turn_input", "again")])
        self.assertIn(("codex", "again"), self.found())

    # -- the watcher, against real processes ---------------------------------

    def agent_in_a_terminal(self, harness="cgrtestagent"):
        """A fake agent run by a shell that owns a pty, like a terminal tab.

        Its name is a harness only inside this test process, so the real
        cgr-watch service on the machine never records it.
        """
        bindir = self.home / "bin"
        bindir.mkdir(exist_ok=True)
        script = bindir / harness
        script.write_text("#!/bin/sh\nsleep 30\n")
        script.chmod(0o755)
        pid, fd = pty.fork()
        if pid == 0:
            os.chdir(str(self.home))
            os.execv("/bin/sh", ["sh", "-c", "%s --fast; sleep 30" % script])
        self.addCleanup(os.close, fd)
        return pid

    def watched_agent(self, w, leader):
        for _ in range(50):
            w.scan()
            mine = [r for r in w.agents.values() if r["leader"] == leader]
            if mine:
                return mine[0]
            time.sleep(0.1)
        self.fail("watcher never saw the agent")

    def reap(self, shell):
        try:
            os.killpg(shell, signal.SIGKILL)
            os.waitpid(shell, 0)
        except (ProcessLookupError, ChildProcessError):
            pass

    def deaths(self):
        try:
            text = (self.cg.watch_dir() / "deaths.jsonl").read_text()
        except OSError:
            return []
        return [json.loads(l) for l in text.splitlines()]

    def test_watcher_ignores_an_agent_you_exit(self):
        w = self.cg.Watcher()
        shell = self.agent_in_a_terminal()
        self.addCleanup(lambda: (os.kill(shell, signal.SIGKILL), os.waitpid(shell, 0)))
        agent = self.watched_agent(w, shell)
        self.assertEqual((agent["harness"], agent["cwd"]), ("cgrtestagent", str(self.home)))
        os.kill(agent["pid"], signal.SIGTERM)
        time.sleep(0.3)
        w.scan()
        w.scan()  # the shell is asked twice
        self.assertEqual(self.deaths(), [])

    def test_watcher_records_an_agent_whose_window_closed(self):
        self.history([(time.time() - 1, "cgrtestagent --fast")])
        w = self.cg.Watcher()
        shell = self.agent_in_a_terminal()
        agent = self.watched_agent(w, shell)
        self.assertEqual(agent["typed"], "cgrtestagent --fast")
        self.assertTrue(json.loads(w.live.read_text()))
        os.killpg(shell, signal.SIGKILL)  # the window goes, shell and agent with it
        os.waitpid(shell, 0)
        time.sleep(0.3)
        w.scan()
        (death,) = self.deaths()
        self.assertEqual((death["pid"], death["typed"]), (agent["pid"], "cgrtestagent --fast"))
        (s,) = self.cg.collect()
        self.assertEqual((s["harness"], s["how"], s["cwd"]),
                         ("cgrtestagent", "window closed", str(self.home)))
        self.assertEqual(self.cg.command(s), "cgrtestagent --fast")  # no "continue" flag

    def test_watcher_dates_a_launch_by_the_clock_now_not_when_it_started(self):
        # The watcher runs for days, and btime slides as it runs (15 minutes in
        # 9 hours on 2026-10-10). Dated by the btime it read at start, a new
        # launch landed outside its history line's window and lost it.
        self.history([(time.time() - 1, "cgrtestagent --fast")])
        real = self.cg.current_boot
        self.cg.current_boot = lambda: (real()[0] + 900, real()[1])
        try:
            w = self.cg.Watcher()
        finally:
            self.cg.current_boot = real
        shell = self.agent_in_a_terminal()
        self.addCleanup(lambda: (os.killpg(shell, signal.SIGKILL), os.waitpid(shell, 0)))
        agent = self.watched_agent(w, shell)
        self.assertLess(abs(agent["started"] - time.time()), 30)
        self.assertEqual(agent["typed"], "cgrtestagent --fast")

    def test_watcher_restart_keeps_what_it_saw_at_launch(self):
        # Restarted hours later, the watcher could only re-date a running agent
        # from its age, which the drift skews; on 2026-10-09 that matched an
        # earlier mistyped line, and the next crash reopened it.
        self.history([(time.time() - 1, "cgrtestagent --fast")])
        shell = self.agent_in_a_terminal()
        self.addCleanup(lambda: (os.killpg(shell, signal.SIGKILL), os.waitpid(shell, 0)))
        first = self.watched_agent(self.cg.Watcher(), shell)
        self.history([])
        again = self.watched_agent(self.cg.Watcher(), shell)
        self.assertEqual((again["started"], again["typed"]),
                         (first["started"], "cgrtestagent --fast"))

    def test_agents_alive_when_an_earlier_boot_ended_went_down_with_it(self):
        d = self.cg.watch_dir()
        d.mkdir(parents=True)
        rec = {"pid": 1, "ticks": "1", "started": self.prev + 100, "harness": "kilo",
               "argv": ["kilo"], "cwd": "/k", "leader": 2, "leader_ticks": "1",
               "boot": "old", "session": None, "typed": "kilo -m fast"}
        (d / "live-old.json").write_text(json.dumps([rec]))
        os.utime(d / "live-old.json", (self.crash, self.crash))
        (d / ("live-%s.json" % self.boot_id)).write_text(json.dumps([dict(rec, cwd="/now")]))
        (s,) = self.cg.collect()
        self.assertEqual((s["how"], s["cwd"]), ("machine went down", "/k"))
        self.assertEqual(self.cg.command(s), "kilo -m fast --continue")

    def watched_boot(self, name, began, agents=None, last=None):
        d = self.cg.watch_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / ("boot-%s" % name)).write_text("%d\n" % began)
        if agents is not None:
            recs = [{"pid": n + 1, "ticks": "1", "started": began + 100, "harness": "kilo",
                     "argv": ["kilo"], "cwd": cwd, "leader": 2, "leader_ticks": "1",
                     "boot": name, "session": None, "typed": "kilo"} for n, cwd in enumerate(agents)]
            (d / ("live-%s.json" % name)).write_text(json.dumps(recs))
            os.utime(d / ("live-%s.json" % name), (last or began + 200,) * 2)

    def test_an_empty_boot_after_the_crash_does_not_hide_it(self):
        # 2026-10-13: a WSL update stopped the VM at 05:05 with six agents
        # running, booted it at 05:06 to finish, and let it idle out at 05:12.
        # At 12:42 "the previous boot" was those five empty minutes, every
        # session was older than it, and cgr said nothing died.
        crashed = self.prev - 86400
        self.write_wtmp([crashed - 86400, crashed, self.prev, self.btime + 90])
        self.watched_boot("crashed", crashed, ["/k"])  # the set last changed early on
        self.watched_boot("empty", self.prev)          # the watcher ran; no agent did
        self.pidfile(999990, "idle", "/w", crashed + 60, 1)
        self.transcript("idle", [prompt("hi"), REPLY], self.prev - 3000)  # quiet its last hour
        found = self.found()
        self.assertIn(("claude", "idle"), found)
        self.assertEqual([s["cwd"] for s in found.values() if s["harness"] == "kilo"], ["/k"])

    def test_a_boot_that_went_down_with_agents_ends_the_look_back(self):
        older = self.prev - 86400
        self.watched_boot("older", older, ["/old"])
        self.watched_boot("crashed", self.prev, ["/new"], last=self.crash)
        self.assertEqual([s["cwd"] for s in self.cg.collect()], ["/new"])

    def test_old_one_shot_records_cannot_stop_the_empty_boot_walk_or_be_restored(self):
        earlier = self.prev - 86400
        self.watched_boot("crashed", earlier, ["/real"])
        self.watched_boot("empty", self.prev)
        r = dict(self.cg.session("omp", None, "/catalog", self.prev + 200, "saved", "models"),
                 boot="empty", argv=["omp", "models"], started=self.prev + 100)
        (self.cg.watch_dir() / "live-empty.json").write_text(json.dumps([r]))
        self.cg.saved_path().write_text(json.dumps({"sessions": [r]}))
        self.assertFalse(self.cg.ended_with_agents("empty"))
        self.assertEqual([s["cwd"] for s in self.cg.collect()], ["/real"])

    def shutdown_at(self, when):
        with self.cg.WTMP.open("ab") as fh:
            fh.write(self.cg.UTMP.pack(1, 0, b"~", b"~~", b"shutdown", b"", 0, 0, 0,
                                      int(when), 0, b"", b""))

    def test_watched_live_set_is_dated_by_shutdown_not_its_last_change(self):
        self.watched_boot("crashed", self.prev, ["/k"], last=self.prev + 200)
        self.shutdown_at(self.crash)
        rec, = self.cg.watched(self.boot_id, self.prev + 500)
        self.assertEqual(rec["last"], self.crash)

    def test_watched_boot_without_shutdown_is_dated_by_the_next_boot(self):
        self.watched_boot("crashed", self.prev, ["/k"], last=self.prev + 200)
        rec, = self.cg.watched(self.boot_id, self.prev)
        self.assertEqual(rec["last"], self.btime)

    def test_watcher_evidence_updates_native_last_for_the_handled_check(self):
        self.pidfile(999990, "again", "/w", self.prev + 60, 1)
        self.transcript("again", [prompt("hi"), REPLY], self.prev + 200)
        self.cg.STATE.mkdir(exist_ok=True)
        (self.cg.STATE / "cgr-handled.json").write_text(json.dumps({"claude:again": self.prev + 500}))
        self.watched_boot("crashed", self.prev)
        r = {"pid": 1, "ticks": "1", "started": self.prev + 100, "harness": "claude",
             "argv": ["claude"], "cwd": "/w", "boot": "crashed", "session": "again"}
        f = self.cg.watch_dir() / "live-crashed.json"
        f.write_text(json.dumps([r])); os.utime(f, (self.prev + 200,) * 2)
        self.shutdown_at(self.crash)
        self.assertEqual(self.found()[("claude", "again")]["last"], self.crash)

    def test_two_dead_agents_in_one_place_without_ids_reopen_once(self):
        d = self.cg.watch_dir()
        d.mkdir(parents=True)
        base = {"ticks": "1", "harness": "opencode", "argv": ["opencode"], "cwd": "/o",
                "leader": 2, "leader_ticks": "1", "boot": self.boot_id, "session": None,
                "typed": "opencode"}
        now = time.time()
        (d / "deaths.jsonl").write_text("".join(
            json.dumps(dict(base, pid=p, started=now - 60 + p, died=now - 10 + p)) + "\n"
            for p in (1, 2)))
        self.assertEqual(len(self.cg.collect()), 1)

    def grouped_crashes(self):
        earlier = self.prev - 86400
        self.watched_boot("earlier", earlier, ["/earlier"])
        self.watched_boot("recent", self.prev, ["/recent"])
        r = {"harness": "kilo", "argv": ["kilo"], "cwd": "/window", "boot": self.boot_id,
             "started": self.btime + 100, "died": self.btime + 200}
        (self.cg.watch_dir() / "deaths.jsonl").write_text(json.dumps(r) + "\n")
        return self.cg.collect(everything=True)

    def test_two_crash_boots_and_a_window_close_form_three_ordered_groups(self):
        items = self.grouped_crashes()
        self.assertEqual(len({s["group"] for s in items}), 3)
        self.assertEqual([s["cwd"] for s in items], ["/window", "/recent", "/earlier"])
        self.assertEqual(items[1]["event"]["boot"], "recent")
        self.assertEqual(items[2]["event"]["end"], self.prev)

    def test_recovery_json_carries_the_event_group_without_changing_its_list_shape(self):
        self.grouped_crashes()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(self.cg.main(["--all", "--json"]), 0)
        items = json.loads(output.getvalue())
        self.assertEqual(len({s["group"] for s in items}), 3)
        self.assertTrue(all("command" in s and "event" in s for s in items))

    def test_old_groups_collapse_in_text_and_can_be_expanded(self):
        self.watched_boot("old", self.prev - 10 * 86400, ["/old"])
        items = self.cg.collect(everything=True)
        # End is the next boot, also ten days old, rather than a live-file mtime.
        self.write_wtmp([self.prev - 10 * 86400, self.prev - 9 * 86400,
                         self.prev, self.btime + 90])
        items = self.cg.collect(everything=True)
        collapsed, expanded = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(collapsed):
            self.cg.show_recovery(items)
        with contextlib.redirect_stdout(expanded):
            self.cg.show_recovery(items, expand_old=True)
        self.assertIn("--expand-old", collapsed.getvalue())
        self.assertNotIn("cd /old", collapsed.getvalue())
        self.assertIn("cd /old", expanded.getvalue())

    def test_picker_can_select_a_whole_group_without_selecting_other_events(self):
        self.watched_boot("recent", self.prev, ["/one", "/two"])
        self.watched_boot("earlier", self.prev - 86400, ["/other"])
        items = self.cg.collect(everything=True)
        self.assertEqual(set(self.picker_keys(items, (b"a", b" ", b"\r"))), {"/one", "/two"})

    def test_picker_arrow_keys_move_to_another_event_header(self):
        self.watched_boot("recent", self.prev, ["/one", "/two"])
        self.watched_boot("earlier", self.prev - 86400, ["/other"])
        items = self.cg.collect(everything=True)
        keys = (b"a", b"\x1b[B", b"\x1b[B", b"\x1b[B", b" ", b"\r")
        self.assertEqual(self.picker_keys(items, keys), ["/other"])

    def test_group_label_carries_stamp_and_count(self):
        items = self.grouped_crashes()
        label = self.cg.group_label(items[0], 2)
        self.assertIn("2 sessions", label)
        self.assertRegex(label, r"20\d\d-\d\d-\d\d \d\d:\d\d")

    def test_select_groups_filters_by_display_number(self):
        items = self.grouped_crashes()
        one = self.cg.select_groups(items, ["1"])
        self.assertEqual(len(one), 1)
        self.assertEqual({s["group"] for s in one}, {items[0]["group"]})
        two = self.cg.select_groups(items, ["2,3"])
        self.assertEqual(len(two), 2)

    def test_preview_line_shows_id_and_prompt(self):
        s = {"id": "abc123", "messages": 42, "model": "opus",
             "last_prompt": "fix the thing", "mid_turn": 1}
        line = self.cg.preview_line(s)
        self.assertIn("abc123", line)
        self.assertIn("42 msgs", line)
        self.assertIn("fix the thing", line)

    def test_brief_prints_one_line_per_group(self):
        items = self.grouped_crashes()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(self.cg.main(["--all", "--brief"]), 0)
        self.assertEqual(len(output.getvalue().strip().splitlines()), 3)

    def test_list_group_flag_restricts_output(self):
        self.grouped_crashes()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(self.cg.main(["--all", "--list", "--group", "2"]), 0)
        self.assertEqual(output.getvalue().count(", 1 session]"), 1)


    def picker_keys(self, items, keys):
        data = self.home / "picker.json"; data.write_text(json.dumps(items))
        script = self.home / "picker.py"
        script.write_text("import json,sys\n" +
                          "sys.path.insert(0, " + repr(str(ROOT / "tests")) + ")\n" +
                          "from test_cgr import load_cgr\n" +
                          "c=load_cgr()\nitems=json.load(open(sys.argv[1]))\n" +
                          "picked=c.choose(items)\nprint('PICKED:'+json.dumps([s['cwd'] for s in picked]))\n")
        master, slave = pty.openpty()
        proc = subprocess.Popen([sys.executable, str(script), str(data)], stdin=slave,
                                stdout=slave, stderr=slave)
        os.close(slave)
        try:
            import select
            output = b""; until = time.monotonic() + 5
            while b"enter reopen" not in output and time.monotonic() < until:
                if select.select([master], [], [], 0.1)[0]:output += os.read(master, 65536)
            self.assertIn(b"enter reopen", output)
            for key in keys:
                os.write(master, key); time.sleep(0.05)
            until = time.monotonic() + 5
            while b"PICKED:" not in output and time.monotonic() < until:
                if select.select([master], [], [], 0.1)[0]:
                    try:output += os.read(master, 65536)
                    except OSError:break
            self.assertEqual(proc.wait(timeout=2), 0)
            picked = json.loads(output.split(b"PICKED:", 1)[1].splitlines()[0])
            return picked
        finally:
            if proc.poll() is None:proc.kill(); proc.wait()
            os.close(master)

    # -- harnesses and history -----------------------------------------------

    def test_harness_of_finds_scripts_and_skips_one_shots(self):
        h = self.cg.harness_of
        self.assertEqual(h(["claude", "--model", "opus"]), ("claude", 0))
        self.assertEqual(h(["node", "--no-warnings", "/x/bin/codex", "-m", "y"]), ("codex", 2))
        self.assertEqual(h(["python3", "/v/bin/hermes"]), ("hermes", 1))
        self.assertIsNone(h(["codex", "exec", "do it"]))
        self.assertIsNone(h(["claude", "-p", "review"]))
        self.assertIsNone(h(["sh", "-c", "claude"]))
        self.assertIsNone(h(["vim", "claude.md"]))

    def test_harness_of_recognizes_installed_generic_pi_and_qwen_entrypoints(self):
        h = self.cg.harness_of
        pi = "/x/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js"
        qwen = "/x/node_modules/@qwen-code/qwen-code/cli-entry.js"
        self.assertEqual(h(["node", pi]), ("pi", 1))
        self.assertEqual(h(["node", qwen]), ("qwen", 1))
        self.assertIsNone(h(["node", pi, "list"]))
        self.assertIsNone(h(["node", qwen, "sessions", "list"]))
        self.assertIsNone(h(["node", "/x/unrelated/cli.js"]))

    def test_harness_subcommands_and_one_shot_flags_are_specific_to_each_cli(self):
        for argv in (["omp", "models"], ["codex", "review"], ["claude", "plugin", "list"],
                     ["hermes", "--oneshot", "hi"], ["pi", "list"], ["gemini", "--list-sessions"],
                     ["qwen", "sessions", "list"], ["agy", "models"], ["jcode", "version"],
                     ["muse", "export", "x"], ["opencode", "models"], ["kilo", "stats"],
                     ["openclaw", "sessions"], ["ante", "--help"], ["omx", "--version"]):
            with self.subTest(argv=argv):
                self.assertIsNone(self.cg.harness_of(argv))
        for argv in (["codex", "-p", "work"], ["jcode", "-p", "provider"],
                     ["claude", "models"], ["codex", "--model", "models"],
                     ["jcode", "repl"], ["opencode", "attach", "http://local"],
                     ["openclaw", "tui"]):
            with self.subTest(argv=argv):
                self.assertIsNotNone(self.cg.harness_of(argv))

    def test_old_watcher_one_shots_are_not_recoverable(self):
        d = self.cg.watch_dir(); d.mkdir(parents=True)
        r = {"harness": "omp", "argv": ["omp", "models"], "cwd": "/w", "boot": self.boot_id,
             "started": time.time() - 100, "died": time.time() - 50}
        (d / "deaths.jsonl").write_text(json.dumps(r) + "\n")
        self.assertEqual(self.cg.collect(), [])

    def test_history_candidate_must_match_the_recorded_process_options(self):
        d = self.cg.watch_dir(); d.mkdir(parents=True)
        t = time.time() - 100
        self.history([(t - 20, "codex --model unrelated")])
        r = {"harness": "codex", "argv": ["codex", "resume", "real"], "cwd": "/w",
             "boot": self.boot_id, "started": t, "died": t + 50, "typed": None}
        (d / "deaths.jsonl").write_text(json.dumps(r) + "\n")
        self.assertEqual(self.cg.command(self.cg.collect()[0]), "codex resume real")

    def test_reopen_without_a_typed_line_exports_its_rebuilt_launch(self):
        s = self.cg.session("codex", "real", "/w", 1, "", "")
        self.assertIn("export CGR_LAUNCH=", self.cg.run_argv(s)[-1])

    def agy_conversation(self, sid, when):
        root = self.home / ".gemini" / "antigravity-cli"
        (root / "presence").mkdir(parents=True, exist_ok=True)
        (root / "conversations").mkdir(exist_ok=True)
        lock = root / "presence" / (sid + ".lock")
        lock.touch(); os.utime(lock, (when,) * 2)
        db = root / "conversations" / (sid + ".db")
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE trajectory_meta (trajectory_id TEXT, cascade_id TEXT)")
        con.execute("INSERT INTO trajectory_meta VALUES ('trajectory', ?)", (sid,))
        con.commit(); con.close()

    def test_agy_resolves_a_unique_presence_lock_near_process_start(self):
        self.agy_conversation("dead", self.prev + 115)
        self.agy_conversation("newer", self.btime + 100)
        r = {"harness": "agy", "cwd": "/w", "started": self.prev + 100,
             "last": self.crash, "how": "machine went down", "argv": ["agy"]}
        self.assertEqual(self.cg.resolve(r, set(), set())["id"], "dead")

    def test_agy_does_not_guess_between_simultaneous_presence_locks(self):
        self.agy_conversation("a", self.prev + 110)
        self.agy_conversation("b", self.prev + 120)
        r = {"harness": "agy", "cwd": "/w", "started": self.prev + 100,
             "last": self.crash, "how": "machine went down", "argv": ["agy"]}
        self.assertIsNone(self.cg.resolve(r, set(), set())["id"])

    def test_resolve_uses_short_resume_flags_and_ignores_future_transcripts(self):
        self.assertEqual(self.cg.argv_id("claude", ["claude", "-r", "named"]), "named")
        self.assertEqual(self.cg.argv_id("opencode", ["opencode", "-s", "named"]), "named")
        for sid, when in (("old", self.crash - 20), ("future", self.btime + 10)):
            jsonl(self.home / ".omp/agent/sessions/-w" / ("date_" + sid + ".jsonl"),
                  [{"type": "session", "id": sid, "cwd": "/w"}, prompt("hi"), REPLY], when)
        r = {"harness": "omp", "cwd": "/w", "started": self.prev + 100,
             "last": self.crash, "how": "machine went down", "argv": ["omp"]}
        self.assertEqual(self.cg.resolve(r, set(), set())["id"], "old")

    def test_a_tab_title_failure_does_not_lose_a_successful_spawn(self):
        s = self.cg.session("codex", "real", "/w", 1, "", "")
        spawn = subprocess.CompletedProcess([], 0, stdout="42\n", stderr="")
        with patch.object(self.cg, "wezterm_window", return_value=1), \
             patch.object(self.cg.subprocess, "run", side_effect=[spawn, subprocess.TimeoutExpired([], 1)]):
            self.assertEqual(list(self.cg.open_wezterm("wezterm", [s])), [s])

    def test_missing_working_directory_is_not_spawned_or_marked_handled(self):
        s = self.cg.session("jcode", "old", str(self.home / "gone"), self.crash, "cut off", "")
        with patch.dict(os.environ, {"TERM_PROGRAM": "WezTerm", "TMUX": "", "WT_SESSION": ""}), \
             patch.object(self.cg.shutil, "which", side_effect=lambda name: "/fake/wezterm" if name == "wezterm" else None), \
             patch.object(self.cg, "open_wezterm", return_value=iter([s])) as opener, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.cg.reopen([s]), 1)
            opener.assert_not_called()
        self.assertEqual(self.cg.load_handled(), {})

    def test_history_unmetafies_and_joins_continued_lines(self):
        # U+2019 is E2 80 99; zsh metafies 0x99 (0x83..0x9f) as 0x83, 0x99 ^ 0x20.
        quote = bytes([0xE2, 0x80, 0x83, 0x99 ^ 0x20])
        (self.home / ".zsh_history").write_bytes(
            b": 100:0;claude don" + quote + b"t\n: 200:0;codex \\\n-m y\n")
        self.assertEqual(self.cg.history(), [(100, "claude don’t"), (200, "codex \n-m y")])

    def test_typed_for_follows_aliases_and_takes_the_closest_launch(self):
        self.zshrc("alias cx='codex --yolo'   # codex, no prompts\nalias ll='ls -l'\n")
        hist = [(90, "cx"), (95, "ll"), (98, "cd ~/p && cx -m y"), (99, "claude")]
        amap = self.cg.aliases()
        self.assertEqual(self.cg.typed_for("codex", 100, hist, amap), "cd ~/p && cx -m y")
        self.assertIsNone(self.cg.typed_for("opencode", 100, hist, amap))

    # -- the command a tab runs ----------------------------------------------

    def cmd(self, harness, sid, typed=None, argv=None):
        s = self.cg.session(harness, sid, "/w", 0, "", "")
        s.update(typed=typed, argv=argv)
        return self.cg.command(s)

    def test_command_keeps_your_flags_and_drops_old_session_flags_and_prompts(self):
        self.assertEqual(
            self.cmd("claude", "new", "claude --dangerously-skip-permissions --resume old 'fix it'"),
            "claude --dangerously-skip-permissions --resume new")

    def test_command_repairs_the_single_dash_resume_typo(self):
        self.assertEqual(self.cmd("claude", "X", "claude -resume"), "claude --resume X")
        self.assertEqual(self.cmd("claude", "X", "claude -resume old"), "claude --resume X")

    def test_command_drops_one_word_prompts_on_every_reopen(self):
        self.assertEqual(self.cmd("codex", "T", "codex continue"), "codex resume T")
        self.assertEqual(self.cmd("codex", "T", argv=["codex", "continue", "resume", "T"]),
                         "codex resume T")
        self.assertEqual(self.cmd("claude", "X", "claude --fast continue"),
                         "claude --fast --resume X")

    def test_command_preserves_option_values_including_spaces(self):
        self.assertEqual(self.cmd("claude", "X", "claude --model opus --name 'my work' continue"),
                         "claude --model opus --name 'my work' --resume X")
        self.assertEqual(self.cmd("codex", "T", "codex -m fast -c 'name=\"my work\"' continue"),
                         "codex -m fast -c 'name=\"my work\"' resume T")
        self.assertEqual(self.cmd("claude", "X", "claude --model=opus --add-dir /a /b --fast continue"),
                         "claude --model=opus --add-dir /a /b --fast --resume X")

    def test_command_drops_prompts_behind_an_alias_and_keeps_env_and_proxy_steps(self):
        self.zshrc("alias cx='codex --yolo'\n")
        self.assertEqual(self.cmd("codex", "T", "cd /w && _proxy_up && BASE=local cx -m fast continue"),
                         "_proxy_up && BASE=local cx -m fast resume T")

    def test_command_does_not_replay_flagged_initial_prompts(self):
        for h, typed, expected in (
                ("agy", "agy --model fast -i hello", "agy --model fast --conversation X"),
                ("gemini", "gemini --prompt-interactive hello", "gemini --resume X"),
                ("qwen", "qwen -i hello", "qwen --resume X"),
                ("opencode", "opencode --prompt hello", "opencode --session X")):
            with self.subTest(harness=h):
                self.assertEqual(self.cmd(h, "X", typed), expected)

    def test_qwen_positional_queries_are_one_shot_but_interactive_prompts_are_sessions(self):
        self.assertIsNone(self.cg.harness_of(["qwen", "hello"]))
        self.assertIsNotNone(self.cg.harness_of(["qwen", "-i", "hello"]))

    def test_command_keeps_launch_variables_and_proxy_steps_but_not_cd(self):
        self.assertEqual(
            self.cmd("claude", "id", "cd ~/x && _proxy_up && BASE=http://127.0.0.1:8082 claude -c"),
            "_proxy_up && BASE=http://127.0.0.1:8082 claude --resume id")
        self.assertEqual(self.cmd("codex", "t", "codex -m y"), "codex -m y resume t")

    def test_command_runs_an_alias_with_resume_unless_it_picks_its_own_session(self):
        self.zshrc("alias cx='codex --yolo'\nalias ccc='claude --continue --fast'\n")
        self.assertEqual(self.cmd("codex", "t", "cx -m y"), "cx -m y resume t")
        self.assertEqual(self.cmd("claude", "id", "ccc"), "ccc")

    def test_command_without_history_uses_the_process_argv(self):
        self.assertEqual(self.cmd("codex", "t", argv=["node", "/x/bin/codex", "-m", "y"]),
                         "codex -m y resume t")
        self.assertEqual(self.cmd("opencode", None, argv=["opencode", "--port", "1"]),
                         "opencode --port 1 --continue")

    def test_command_with_shell_syntax_is_left_alone(self):
        self.assertEqual(self.cmd("claude", "id", 'claude --model "$M"'),
                         'claude --model "$M" --resume id')

    # -- indexed recovery ----------------------------------------------------

    def indexed_row(self, harness, sid, **kw):
        r = self.cg.entry(harness, sid, cwd="/w", started=self.prev + 100)
        r.update(last=self.crash, status="cut off", how="status Crashed", source="cli",
                 interactive=1, in_store=1, first_seen=time.time(), last_seen=time.time())
        r.update(kw)
        return r

    def test_indexed_jcode_and_muse_cut_off_sessions_reach_recovery(self):
        self.cg.write_index([self.indexed_row("jcode", "j"), self.indexed_row("muse", "m")])
        found = self.found(everything=True)
        self.assertEqual(set(found), {("jcode", "j"), ("muse", "m")})
        self.assertEqual(self.cg.command(found[("jcode", "j")]), "jcode --resume j")
        self.assertEqual(self.cg.command(found[("muse", "m")]), "muse resume m")

    def test_index_recovery_requires_interactive_cut_off_rows_still_in_store(self):
        rows = [self.indexed_row("jcode", "good"),
                self.indexed_row("jcode", "unknown", status="unknown", mid_turn=1),
                self.indexed_row("jcode", "bot", interactive=0),
                self.indexed_row("jcode", "gone", in_store=0),
                self.indexed_row("jcode", "missing", path=str(self.home / "missing.json")),
                self.indexed_row("muse", "@123"),
                self.indexed_row("unrecognized", "bad")]
        self.cg.write_index(rows)
        self.assertEqual(set(self.found(everything=True)), {("jcode", "good")})

    def test_index_recovery_merges_with_watcher_and_obeys_the_default_window(self):
        self.cg.write_index([self.indexed_row("jcode", "same"),
                             self.indexed_row("jcode", "old", last=self.prev - 500)])
        self.watched_boot("crashed", self.prev)
        r = {"harness": "jcode", "argv": ["jcode", "--resume", "same"], "cwd": "/w",
             "boot": "crashed", "started": self.prev + 100}
        (self.cg.watch_dir() / "live-crashed.json").write_text(json.dumps([r]))
        found = self.found()
        self.assertEqual(set(found), {("jcode", "same")})
        self.assertEqual(found[("jcode", "same")]["last"], self.btime)

    def test_current_boot_no_end_record_is_not_a_death_without_watcher_evidence(self):
        self.cg.write_index([self.indexed_row("muse", "idle", how="no end record",
                                             last=self.btime + 100)])
        self.assertEqual(self.cg.collect(), [])

    def jcode_store(self, sid, when):
        p = self.home / ".jcode/sessions" / (sid + ".json")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"id": sid, "working_dir": "/w", "created_at": self.prev + 100,
                                 "last_active_at": when, "status": {"Crashed": {"message": "killed"}},
                                 "messages": [{"role": "user", "content": "finish"}]}))
        os.utime(p, (when,) * 2)

    def test_stale_index_refreshes_once_without_feeding_itself(self):
        self.cg.write_index([self.indexed_row("jcode", "stale")])
        os.utime(self.cg.db_path(), (0, 0))
        self.jcode_store("fresh", self.crash)
        with patch.object(self.cg, "build_index", wraps=self.cg.build_index) as build:
            self.assertIn(("jcode", "fresh"), self.found(everything=True))
            self.assertEqual(build.call_count, 1)
        self.assertGreater(self.cg.db_path().stat().st_mtime, time.time() - 60)

    def test_a_fresh_index_is_reused_and_index_build_does_not_trust_old_index_evidence(self):
        self.cg.write_index([self.indexed_row("jcode", "cached")])
        with patch.object(self.cg, "build_index", side_effect=AssertionError("unexpected refresh")):
            self.assertIn(("jcode", "cached"), self.found(everything=True))
        rows, _ = self.cg.build_index()
        self.assertNotIn("cached", {r["id"] for r in rows})

    def test_watcher_resolves_an_unnamed_jcode_from_its_native_store(self):
        self.jcode_store("actual", self.crash)
        r = {"harness": "jcode", "argv": ["jcode"], "cwd": "/w",
             "started": self.prev + 100, "last": self.crash, "how": "machine went down"}
        self.assertEqual(self.cg.resolve(r, set(), set())["id"], "actual")

    def test_openclaw_indexed_recovery_names_the_session_in_its_resume_query(self):
        self.cg.write_index([self.indexed_row("openclaw", "uuid", how="status failed")])
        found = self.found(everything=True)
        self.assertEqual(self.cg.command(found[("openclaw", "uuid")]), "openclaw resume uuid")

    def test_history_keeps_a_watcher_resolved_id_even_without_a_native_indexer(self):
        self.agy_conversation("actual", self.prev + 115)
        self.watched_boot("crashed", self.prev)
        r = {"harness": "agy", "argv": ["agy"], "cwd": "/w",
             "boot": "crashed", "started": self.prev + 100}
        (self.cg.watch_dir() / "live-crashed.json").write_text(json.dumps([r]))
        rows, _ = self.cg.build_index()
        agy = [r for r in rows if r["harness"] == "agy"]
        self.assertEqual([(r["id"], r["status"]) for r in agy], [("actual", "cut off")])
        self.assertIn("--conversation actual", agy[0]["resume"])

    # -- handled -------------------------------------------------------------

    def test_reopened_sessions_stay_hidden_until_they_die_again(self):
        self.pidfile(999990, "loop", "/w", self.prev + 60, 1)
        self.transcript("loop", [prompt("hi"), REPLY], self.crash)
        self.cg.mark_handled(self.cg.collect())
        self.assertEqual(self.cg.collect(), [])
        later = time.time() + 5
        path = self.home / ".claude" / "projects" / "-w" / "loop.jsonl"
        os.utime(path, (later, later))
        self.pidfile(999989, "loop", "/w", self.btime + 60, 1)
        self.assertEqual([s["id"] for s in self.cg.collect()], ["loop"])

    def test_state_is_owner_only_and_loose_files_are_repaired(self):
        # It holds argv, cwds and typed command lines: the T21 rule, as for history.jsonl.
        d = self.cg.watch_dir()
        d.mkdir(parents=True, mode=0o775)
        os.chmod(d, 0o775)
        (d / "deaths.jsonl").write_text("")
        os.chmod(d / "deaths.jsonl", 0o664)  # written before the rule existed
        w = self.cg.Watcher()
        rec = {"pid": 1, "started": time.time(), "harness": "kilo", "cwd": "/k"}
        w.agents[1] = rec
        w.write_live()
        w.died(rec, time.time())
        self.cg.mark_handled([self.cg.session("kilo", "x", "/k", time.time(), "", "")])
        mode = lambda p: os.stat(p).st_mode & 0o777
        self.assertEqual((mode(self.cg.STATE), mode(d)), (0o700, 0o700))
        for f in (w.live, w.deaths, self.cg.STATE / "cgr-handled.json"):
            self.assertEqual(mode(f), 0o600, f.name)

    def test_another_accounts_agents_are_neither_recorded_nor_trusted(self):
        # Its argv is the other account's to choose (`claude --mcp-config=...`),
        # and reopening it would run that as you.
        shell = self.agent_in_a_terminal()
        self.addCleanup(lambda: (os.killpg(shell, signal.SIGKILL), os.waitpid(shell, 0)))
        agent = self.watched_agent(self.cg.Watcher(), shell)  # seen while it is ours
        self.cg.ME = os.getuid() + 1                            # now it is someone else's
        try:
            w = self.cg.Watcher()
            for _ in range(3):
                w.scan()
            self.assertNotIn(agent["pid"], w.agents)
            self.assertNotIn("--fast", self.cg.running_words())
        finally:
            self.cg.ME = os.getuid()

    def test_state_files_another_account_owns_are_not_read(self):
        # A typed line in them is run as a shell command when reopened.
        d = self.cg.watch_dir()
        d.mkdir(parents=True)
        rec = {"pid": 1, "ticks": "1", "started": self.prev + 100, "harness": "kilo",
               "argv": ["kilo"], "cwd": "/k", "leader": 2, "leader_ticks": "1",
               "boot": "old", "session": None, "typed": "kilo; touch /tmp/owned"}
        (d / "live-old.json").write_text(json.dumps([rec]))
        os.utime(d / "live-old.json", (self.crash, self.crash))
        self.assertEqual(len(self.cg.collect()), 1)  # ours: read
        self.cg.ME = os.getuid() + 1
        try:
            self.assertEqual(self.cg.collect(), [])
        finally:
            self.cg.ME = os.getuid()

    def test_titles_and_commands_cannot_send_escape_sequences(self):
        # Titles come from prompts and model-written thread names; an escape in
        # one could rewrite the screen or the clipboard while you pick.
        s = self.cg.session("codex", "t", "/p\x1b]0;x\x07", time.time(), "machine went down",
                            "fix\x1b[2J\x1b]52;c;ZXZpbA==\x07 it\x9b")
        s["typed"] = "codex -m y\x1b[8m"
        for text in (self.cg.row(s), self.cg.tab_title(s), self.cg.runnable(s)):
            self.assertFalse([c for c in text if ord(c) < 32 or 127 <= ord(c) < 160], repr(text))
        self.assertIn("fix?[2J", self.cg.row(s))

    # -- omp and command lines -----------------------------------------------

    def omp_session(self, sid, records, when, cwd="/m"):
        head = [{"type": "session", "id": sid, "cwd": cwd, "timestamp": "2026-10-01T10:00:00Z"}]
        jsonl(self.home / ".omp" / "agent" / "sessions" / "-m" / ("2026_%s.jsonl" % sid),
              head + records, when)

    def test_omp_closed_by_a_signal_is_cut_off_and_dispose_is_not(self):
        # omp writes session_exit on SIGHUP too, naming the signal (2026-09-12:
        # five tabs at once); cgr took any session_exit for a deliberate one.
        say = {"type": "message", "message": {"role": "user", "content": [
            {"type": "text", "text": "go"}]}}
        self.omp_session("hup", [say, exit_marker("sighup")], self.crash)
        self.omp_session("bye", [say, exit_marker("dispose")], self.crash)
        found = self.found()
        self.assertEqual(found[("omp", "hup")]["how"], "window closed")
        self.assertNotIn(("omp", "bye"), found)

    def test_omp_nested_signal_reason_is_cut_off_and_resolvable(self):
        # Live omp writes the signal nested (`data.reason`, seen 2026-10-17:
        # every saved window ended `{"customType": "session_exit", "data":
        # {"reason": "sighup"}}`); a flat-only read dropped the transcript and
        # left the watcher's id-less row doubled beside the saved one.
        nested = {"type": "custom", "customType": "session_exit",
                  "data": {"reason": "sighup", "kind": "signal"}}
        say = {"type": "message", "message": {"role": "user", "content": [
            {"type": "text", "text": "go"}]}}
        self.omp_session("nested", [say, nested], self.crash)
        found = self.found()
        self.assertEqual(found[("omp", "nested")]["how"], "window closed")
        r = {"harness": "omp", "cwd": "/m", "started": self.prev + 100,
             "last": self.crash, "how": "window closed", "argv": ["omp"]}
        self.assertEqual(self.cg.resolve(r, set(), set())["id"], "nested")

    def test_session_exit_reason_reads_flat_nested_and_string_shapes(self):
        # exit_reason() and tail_scan() each carried their own copy of this
        # rule and neither read a string-shaped `data`.
        reason = self.cg.session_exit_reason
        self.assertEqual(reason({"reason": "other"}), "other")
        self.assertEqual(reason({"data": {"reason": "sighup"}}), "sighup")
        self.assertEqual(reason({"data": "sigterm"}), "sigterm")
        self.assertEqual(reason({"data": None, "reason": None}), "unknown")
        self.assertEqual(reason({"data": {}}), "unknown")
        self.assertEqual(reason({"data": ""}), "unknown")

    def test_exit_reason_and_tail_scan_agree_on_string_shaped_data(self):
        say = {"type": "message", "message": {"role": "user", "content": [
            {"type": "text", "text": "go"}]}}
        rec = {"type": "custom", "customType": "session_exit", "data": "sighup"}
        self.omp_session("flat", [say, rec], self.crash)
        (path,) = (self.home / ".omp" / "agent" / "sessions" / "-m").glob("*flat.jsonl")
        self.assertEqual(self.cg.exit_reason(path), "sighup")
        self.assertEqual(self.cg.tail_scan(path)[0], "sighup")

    def test_saved_record_absorbs_the_watchers_id_less_row(self):
        # The watcher's id-less row and the saved record are the same window
        # (2026-10-17: four saved omp windows each showed twice, as `saved`
        # plus a `window closed` twin with no id).
        say = {"type": "message", "message": {"role": "user", "content": [
            {"type": "text", "text": "go"}]}}
        nested = {"type": "custom", "customType": "session_exit",
                  "data": {"reason": "sighup"}}
        self.omp_session("kept", [say, nested], self.crash)
        started = self.prev + 100
        watcher_row = dict(self.cg.session("omp", None, "/m", self.crash, "window closed", ""),
                           started=started, typed="omp", argv=["omp"])
        saved_row = dict(self.cg.session("omp", "kept", "/m", self.crash, "saved", "kept"),
                         started=started)
        items = {self.cg.item_key(watcher_row): watcher_row}
        self.cg.merge(items, saved_row)
        self.assertEqual(list(items), [("omp", "kept")])

    def saved_omp_in_closed_boot(self, how="machine went down"):
        """A `cgr --save` omp window that then died in the boot before this one,
        with the watcher's row for the same window resolving to the same id."""
        say = {"type": "message", "message": {"role": "user", "content": [
            {"type": "text", "text": "go"}]}}
        self.omp_session("kept", [say], self.crash)
        started = self.prev + 100
        rec = {"pid": 1, "ticks": "1", "started": started, "harness": "omp", "argv": ["omp"],
               "cwd": "/m", "leader": 2, "leader_ticks": "1", "boot": "crashed",
               "session": None, "typed": "omp"}
        self.watched_boot("crashed", self.prev)
        if how == "window closed":
            (self.cg.watch_dir() / "deaths.jsonl").write_text(
                json.dumps(dict(rec, boot="crashed", died=self.crash - 30)) + "\n")
        else:
            f = self.cg.watch_dir() / "live-crashed.json"
            f.write_text(json.dumps([rec]))
            os.utime(f, (self.prev + 200,) * 2)
        row = dict(self.cg.session("omp", "kept", "/m", self.prev + 150, "saved", "kept"),
                   started=started, boot="crashed", pid=1, ticks="1", typed="omp", argv=["omp"])
        self.cg.private_dir(self.cg.STATE)
        self.cg.saved_path().write_text(json.dumps({"saved": self.prev + 150, "sessions": [row]}))

    def test_saved_row_stays_saved_on_a_closed_boot_period(self):
        # 2026-10-18 audit: `cgr --save` rows never rendered green after the
        # restart; the watcher's row for the same id won the merge and the
        # group fell through to "machine went down" / "window closed".
        for how in ("machine went down", "window closed"):
            with self.subTest(how=how):
                self.saved_omp_in_closed_boot(how)
                (s,) = self.cg.collect(everything=True)
                self.assertEqual((s["id"], s["how"], s["event"]["kind"]), ("kept", "saved", "saved"))
                self.assertEqual(self.cg.how_color(s), "32")
                self.assertTrue(s["group"].startswith("saved:"))

    def test_saved_row_is_green_in_list_and_brief(self):
        self.saved_omp_in_closed_boot()
        for flag in ("--list", "--brief"):
            out = io.StringIO()
            with patch.object(self.cg, "USE_COLOR", True), contextlib.redirect_stdout(out):
                self.assertEqual(self.cg.main(["--all", flag]), 0)
            self.assertIn("\x1b[1;32msaved at", out.getvalue(), flag)

    def test_merge_keeps_saved_whichever_row_arrives_last(self):
        watcher = dict(self.cg.session("omp", "x", "/m", self.crash, "window closed", "t"),
                       started=self.prev, boot="b")
        saved_row = dict(self.cg.session("omp", "x", "/m", self.prev + 10, "saved", "t"),
                         started=self.prev, boot="b")
        for first, second in ((watcher, saved_row), (saved_row, watcher)):
            items = {}
            self.cg.merge(items, dict(first))
            self.cg.merge(items, dict(second))
            self.assertEqual(items[("omp", "x")]["how"], "saved")
            self.assertEqual(items[("omp", "x")]["last"], self.crash)

    def test_history_retains_saved_evidence_and_its_date_renders_green(self):
        # 2026-10-18 audit: build_index() dropped every `saved` row from its
        # evidence, so the DB never held how=saved and no date could be green.
        self.saved_omp_in_closed_boot()
        r = self.history_rows()[("omp", "kept")]
        self.assertEqual((r["status"], r["how"]), ("cut off", "saved"))
        self.assertTrue(self.cg.flagged(r))
        with patch.object(self.cg, "USE_COLOR", True):
            self.assertIn("1;32m", self.cg.history_group_label("2026-10-17", [r]))

    def test_history_saved_evidence_outranks_a_later_clean_exit_record(self):
        # You named this exact session for reopening; exiting after a save is
        # the planned restart the save was for, so it stays saved (green).
        say = {"type": "message", "message": {"role": "user", "content": [
            {"type": "text", "text": "go"}]}}
        self.omp_session("bye", [say, exit_marker("dispose")], self.crash)
        row = dict(self.cg.session("omp", "bye", "/m", self.prev + 150, "saved", "bye"),
                   started=self.prev + 100, boot="old", pid=1, ticks="1", typed="omp")
        self.cg.private_dir(self.cg.STATE)
        self.cg.saved_path().write_text(json.dumps({"saved": self.prev + 150, "sessions": [row]}))
        r = self.history_rows()[("omp", "bye")]
        self.assertEqual((r["status"], r["how"]), ("cut off", "saved"))

    def test_history_group_label_colors_by_the_dominant_kind_and_counts_the_rest(self):
        # One old crash must not turn a nine-saved date red, nor hide itself.
        row = lambda how: {"how": how, "last": self.crash}
        label = lambda *hows: self.cg.history_group_label(
            "2026-10-17", [row(h) for h in hows])
        with patch.object(self.cg, "USE_COLOR", True):
            self.assertIn("\x1b[1;32m", label("saved", "saved"))
            self.assertIn("\x1b[1;33m", label("window closed", "killed", "window closed"))
            self.assertIn("\x1b[1;31m", label("machine went down", "saved"))   # tie: the worse
            mixed = label("saved", "saved", "saved", "machine went down")
            self.assertIn("\x1b[1;32m", mixed)
            self.assertIn("3 saved, 1 crash", mixed)
        self.assertNotIn(" saved", label("saved", "saved"))   # a uniform date needs no breakdown

    def absorb(self, cur_over, saved_over):
        """Merge a saved omp row into one id-less watcher row; True if it absorbed."""
        base = self.cg.session("omp", None, "/m", self.crash, "window closed", "")
        cur = {**base, "started": self.prev + 100, "typed": "omp", "argv": ["omp"], **cur_over}
        sav = {**self.cg.session("omp", "kept", "/m", self.prev + 50, "saved", "kept"),
               "started": self.prev + 100, "typed": "omp", **saved_over}
        items = {self.cg.item_key(cur): cur}
        self.cg.merge(items, sav)
        return ("omp", "kept") in items and len(items) == 1

    def test_saved_absorb_window_boundary_is_299_seconds(self):
        self.assertTrue(self.absorb({"started": self.prev + 100 + 299}, {}))
        self.assertFalse(self.absorb({"started": self.prev + 100 + 300}, {}))

    def test_saved_absorb_requires_the_same_process_when_both_name_one(self):
        self.assertTrue(self.absorb({"pid": 7, "ticks": "9"}, {"pid": 7, "ticks": "9"}))
        self.assertFalse(self.absorb({"pid": 7, "ticks": "9"}, {"pid": 8, "ticks": "9"}))
        self.assertFalse(self.absorb({"pid": 7, "ticks": "9"}, {"pid": 7, "ticks": "10"}))

    def test_saved_absorb_requires_the_same_typed_line_when_both_have_one(self):
        self.assertFalse(self.absorb({"typed": "omp --model a"}, {"typed": "omp --model b"}))
        self.assertTrue(self.absorb({"typed": None}, {"typed": "omp --model b"}))

    def test_saved_absorb_takes_the_nearest_start_not_the_first_row(self):
        far = dict(self.cg.session("omp", None, "/m", self.crash, "window closed", ""),
                   started=self.prev + 100 - 250, typed="omp")
        near = dict(far, started=self.prev + 100 + 20)
        items = {}
        self.cg.merge(items, far)
        self.cg.merge(items, near)
        sav = dict(self.cg.session("omp", "kept", "/m", self.prev + 50, "saved", "kept"),
                   started=self.prev + 100)
        self.cg.merge(items, sav)
        self.assertEqual(items[("omp", "kept")]["started"], self.prev + 100)
        (rest,) = [v for k, v in items.items() if k != ("omp", "kept")]
        self.assertEqual(rest["started"], self.prev + 100 - 250)

    def test_resolved_watcher_row_carries_its_pid_and_ticks(self):
        r = {"harness": "omp", "cwd": "/m", "started": self.prev + 100, "last": self.crash,
             "how": "window closed", "argv": ["omp"], "pid": 41, "ticks": "77"}
        s = self.cg.resolve(r, set(), set())
        self.assertEqual((s["pid"], s["ticks"]), (41, "77"))

    def test_omp_resolve_pairs_each_window_with_its_own_transcript_in_any_order(self):
        # Two bare omp windows in one directory: the older process must not
        # adopt the newer transcript just because it was touched last.
        import datetime
        iso = lambda t: datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        say = {"type": "message", "message": {"role": "user", "content": [
            {"type": "text", "text": "go"}]}}
        t = int(self.prev)
        for sid, began, touched in (("older", t + 10, t + 500), ("newer", t + 1010, t + 1500)):
            head = {"type": "session", "id": sid, "cwd": "/m", "timestamp": iso(began)}
            jsonl(self.home / ".omp" / "agent" / "sessions" / "-m" / ("2026_%s.jsonl" % sid),
                  [head, say], touched)
        rec = lambda started: {"harness": "omp", "cwd": "/m", "started": started, "last": t + 2000,
                               "how": "window closed", "argv": ["omp"]}
        first, second = rec(t), rec(t + 1000)
        for order in ((first, second), (second, first)):
            taken, got = set(), {}
            for r in order:
                s_ = self.cg.resolve(r, set(), taken)
                taken.add(self.cg.item_key(s_))
                got[r["started"]] = s_["id"]
            self.assertEqual(got, {t: "older", t + 1000: "newer"})

    # -- the history browser: caps, selection, previews, collapse -----------

    def noon(self, days_ago):
        d = time.localtime()
        return time.mktime((d.tm_year, d.tm_mon, d.tm_mday - days_ago, 12, 0, 0, 0, 0, -1))

    def dated_rows(self, plan, harness="jcode", **kw):
        """{days_ago: count} -> one cut-off row per count, all on that local date."""
        rows = []
        for days, n in plan.items():
            for i in range(n):
                rows.append(self.indexed_row(harness, "d%d-%d" % (days, i),
                                             last=self.noon(days) - i * 10, **kw))
        return rows

    def history_cli(self, rows, *args):
        out = io.StringIO()
        with patch.object(self.cg, "build_index", return_value=(rows, set())), \
                contextlib.redirect_stdout(out):
            rc = self.cg.main(["--history", *args])
        return rc, out.getvalue()

    def test_history_cap_ends_on_a_date_boundary_and_the_header_counts_both(self):
        # 2026-10-18 audit: bad[:40] cut a date mid-way and printed it as if
        # complete ("[2026-10-13, 3 sessions]" with more behind it).
        rc, out = self.history_cli(self.dated_rows({0: 30, 1: 15, 2: 5}), "--list")
        self.assertEqual(rc, 0)
        self.assertIn(", 15 sessions", out)          # the straddling date is whole
        self.assertNotIn(", 5 sessions", out)        # the next date is left for --all
        self.assertIn("showing 45 of 50", out)
        rc, out = self.history_cli(self.dated_rows({0: 30, 1: 15, 2: 5}), "--list", "--all")
        self.assertIn(", 5 sessions", out)
        self.assertNotIn("showing", out)

    def test_history_group_and_day_select_a_date_without_a_terminal(self):
        rows = self.dated_rows({0: 2, 1: 3, 2: 1})
        _, out = self.history_cli(rows, "--list", "--group", "2")
        self.assertIn(", 3 sessions", out)
        self.assertNotIn(", 2 sessions", out)
        self.assertNotIn(", 1 session]", out)
        day = time.strftime("%Y-%m-%d", time.localtime(self.noon(2)))
        _, out = self.history_cli(rows, "--list", "--day", day)
        self.assertIn(", 1 session", out)
        self.assertNotIn(", 3 sessions", out)
        rc, out = self.history_cli(rows, "--list", "--group", "9")
        self.assertIn("no sessions in group(s) 9", out)

    def test_history_yes_reopens_only_a_selected_date(self):
        rows = self.dated_rows({0: 2, 1: 3})
        rc, out = self.history_cli(rows, "--yes")
        self.assertEqual(rc, 1)                        # still refused without a selection
        with patch.object(self.cg, "reopen", return_value=0) as reopen:
            with patch.object(self.cg, "build_index", return_value=(rows, set())), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.cg.main(["--history", "--yes", "--group", "2"]), 0)
        self.assertEqual({s["id"] for s in reopen.call_args[0][0]}, {"d1-0", "d1-1", "d1-2"})

    def test_history_preview_prints_a_line_under_each_session(self):
        rows = self.dated_rows({0: 1}, messages=42, model="opus", last_prompt="fix the thing")
        _, plain = self.history_cli(rows, "--list")
        _, out = self.history_cli(rows, "--list", "--preview")
        self.assertNotIn("42 msgs", plain)
        self.assertIn("42 msgs", out)
        self.assertIn("fix the thing", out.split("42 msgs")[1])

    def test_history_old_dates_collapse_until_expanded_or_selected(self):
        rows = self.dated_rows({0: 1, 10: 2})
        for r in rows:
            r["last_prompt"] = "oldnote" if r["id"].startswith("d10-") else "freshnote"
        _, out = self.history_cli(rows, "--list")
        self.assertIn("[collapsed; --expand-old shows sessions]", out)
        self.assertEqual((out.count("oldnote"), out.count("freshnote")), (0, 1))
        _, out = self.history_cli(rows, "--list", "--expand-old")
        self.assertNotIn("collapsed", out)
        self.assertEqual(out.count("oldnote"), 2)
        day = time.strftime("%Y-%m-%d", time.localtime(self.noon(10)))
        _, out = self.history_cli(rows, "--list", "--day", day)
        self.assertEqual(out.count("oldnote"), 2)

    def test_history_picker_shows_the_preview_line_and_returns_the_pick(self):
        rows = self.dated_rows({0: 2}, messages=42, last_prompt="fix the thing",
                               resume="cd /w && jcode --resume ZZ")
        data = self.home / "rows.json"
        data.write_text(json.dumps(rows))
        script = self.home / "hpicker.py"
        script.write_text("import json,sys\n" +
                          "sys.path.insert(0, " + repr(str(ROOT / "tests")) + ")\n" +
                          "from test_cgr import load_cgr\n" +
                          "c=load_cgr()\nrows=json.load(open(sys.argv[1]))\n" +
                          "got=c.choose_history(rows, c.history_groups(rows), preview=True)\n" +
                          "print('PICKED:'+json.dumps([r['id'] for r in got]))\n")
        master, slave = pty.openpty()
        proc = subprocess.Popen([sys.executable, str(script), str(data)], stdin=slave,
                                stdout=slave, stderr=slave)
        os.close(slave)
        try:
            import select
            output = b""

            def read_until(marker):
                until = time.monotonic() + 5
                nonlocal output
                while marker not in output and time.monotonic() < until:
                    if select.select([master], [], [], 0.1)[0]:
                        try:
                            output += os.read(master, 65536)
                        except OSError:
                            break

            read_until(b"enter reopen")
            for key in (b" ", b" ", b"\r"):   # open the date (the cursor enters it), pick the first
                os.write(master, key)
                time.sleep(0.05)
            read_until(b"PICKED:")
            self.assertEqual(proc.wait(timeout=2), 0)
            self.assertIn(b"42 msgs", output)
            self.assertIn(b"jcode --resume ZZ", output)   # a long row no longer cuts its second line
            self.assertEqual(json.loads(output.split(b"PICKED:", 1)[1].splitlines()[0]), ["d0-0"])
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            os.close(master)

    def test_history_hides_id_less_rows_of_every_harness(self):
        # `@` rows have no id, so no picker can reopen them; they are not
        # written at all (write_index purges them), hence absent everywhere.
        rows = self.dated_rows({0: 1}) + [
            self.indexed_row("kilo", "@100", last=self.noon(0)),
            self.indexed_row("agy", "@200", last=self.noon(0))]
        self.cg.write_index(rows)
        _, out = self.history_cli([r for r in rows if not r["id"].startswith("@")], "--list")
        self.assertIn(", 1 session", out)
        self.assertNotIn("kilo", out.split("cut off or left")[-1])
        con = sqlite3.connect(str(self.cg.db_path()))
        self.addCleanup(con.close)
        self.assertEqual([r[0] for r in con.execute("SELECT id FROM sessions WHERE id LIKE '@%'")], [])

    def test_reopen_history_skips_an_id_less_row(self):
        row = dict(self.indexed_row("kilo", "@100"), typed="kilo")
        err = io.StringIO()
        with patch.object(self.cg, "reopen", return_value=0) as reopen, \
                contextlib.redirect_stderr(err):
            self.cg.reopen_history([row])
        self.assertEqual(reopen.call_args[0][0], [])
        self.assertIn("@100", err.getvalue())

    def test_write_index_purges_all_stale_id_less_rows(self):
        # Older cgr minted `@` rows as evidence placeholders; no picker can
        # reopen them by id, so every write deletes them, any harness.
        stale = [self.indexed_row(h, "@100") for h in ("claude", "omp", "kilo", "codex")]
        keep = [self.indexed_row("omp", "real")]
        self.cg.write_index(stale)
        self.cg.write_index(keep)
        con = sqlite3.connect(str(self.cg.db_path()))
        self.addCleanup(con.close)
        self.assertEqual(sorted(con.execute("SELECT harness, id FROM sessions")),
                         [("omp", "real")])

    def test_reopen_history_keeps_what_the_row_knows(self):
        row = dict(self.indexed_row("omp", "abc", messages=7, model="m", mid_turn=1,
                                    last_prompt="do it", typed=None),
                   argv=json.dumps(["omp", "--model", "fast"]))
        with patch.object(self.cg, "reopen", return_value=0) as reopen:
            self.cg.reopen_history([row])
        (s,) = reopen.call_args[0][0]
        self.assertEqual((s["started"], s["messages"], s["model"], s["mid_turn"], s["last_prompt"]),
                         (row["started"], 7, "m", 1, "do it"))
        self.assertEqual(self.cg.command(s), "omp --model fast --resume abc")
        live = self.cg.session("omp", "abc", "/w", 0, "x", "")
        self.assertEqual(self.cg.key(s), self.cg.key(live))   # reopened here, hidden in --list

    def test_history_stores_the_launch_argv_of_its_evidence(self):
        self.saved_omp_in_closed_boot()
        r = self.history_rows()[("omp", "kept")]
        self.assertEqual(json.loads(r["argv"]), ["omp"])
        self.assertIn("argv", self.cg.COLUMNS)

    def test_history_groups_by_date_and_reopens_picks(self):
        t = self.crash
        rows = []
        for sid, when in (("today-a", t), ("today-b", t - 100), ("yesterday", t - 86400)):
            r = self.indexed_row("jcode", sid, last=when)
            rows.append(r)
        self.cg.write_index(rows)
        groups = self.cg.history_groups([dict(zip(self.cg.COLUMNS, t_))
                                         for t_ in self.cg.write_index(rows, set())])
        self.assertEqual([len(g) for _, g in groups], [2, 1])
        picked = [r for day, grows in groups for r in grows if r["id"] == "today-a"]
        with patch.object(self.cg, "reopen", return_value=0) as reopen:
            self.assertEqual(self.cg.reopen_history(picked), 0)
            self.assertEqual(reopen.call_args[0][0][0]["id"], "today-a")
            self.assertIn("--resume", self.cg.command(reopen.call_args[0][0][0]))

    def test_a_session_named_on_the_command_line_is_the_one_reopened(self):
        # `codex resume <id>` ran that thread, whichever thread in its directory
        # was touched last (2026-10-10: its own subagents were).
        t = int(time.time())
        self.codex_db([("named", "/p", "the work", t - 50, t - 9000, "cli", "user"),
                       ("newer", "/p", "other", t - 5, t - 60, "cli", "user")], [])
        rec = {"harness": "codex", "cwd": "/p", "started": t - 100, "last": t, "how": "x",
               "argv": ["node", "/n/bin/codex", "-m", "y", "resume", "named"], "typed": None}
        s = self.cg.resolve(rec, set(), set())
        self.assertEqual((s["id"], s["title"]), ("named", "the work"))

    # -- a planned restart (cgr --save) ----------------------------------------

    def test_save_records_what_runs_and_lists_it_once_it_is_gone(self):
        self.history([(time.time() - 1, "cgrtestagent --fast")])
        shell = self.agent_in_a_terminal()
        self.addCleanup(self.reap, shell)
        self.watched_agent(self.cg.Watcher(), shell)  # wait until it runs
        (s,) = [x for x in self.cg.save() if x["harness"] == "cgrtestagent"]
        self.assertEqual((s["cwd"], s["typed"]), (str(self.home), "cgrtestagent --fast"))
        self.assertEqual(os.stat(self.cg.saved_path()).st_mode & 0o777, 0o600)
        self.assertEqual([x for x in self.cg.collect() if x["harness"] == "cgrtestagent"], [])
        self.reap(shell)  # exited, closed or rebooted: all the same now
        time.sleep(0.2)
        (back,) = [x for x in self.cg.collect() if x["harness"] == "cgrtestagent"]
        self.assertEqual((back["how"], self.cg.command(back)), ("saved", "cgrtestagent --fast"))
        self.cg.mark_handled([back])
        self.assertEqual([x for x in self.cg.collect() if x["harness"] == "cgrtestagent"], [])

    def test_saved_sessions_from_the_boot_before_are_listed_by_id(self):
        sessions = [dict(self.cg.session("codex", "t1", "/p", self.crash, "saved", "work"),
                         pid=1, ticks="1", boot="old", typed="codex -m y")]
        self.cg.private_dir(self.cg.STATE)
        self.cg.saved_path().write_text(json.dumps({"saved": self.crash, "sessions": sessions}))
        (s,) = self.cg.collect()
        self.assertEqual((s["how"], self.cg.command(s)), ("saved", "codex -m y resume t1"))

    # -- every session, in one database (cgr --history) ----------------------

    def history_rows(self):
        rows, failed = self.cg.build_index()
        self.assertEqual(failed, set())
        out = [dict(zip(self.cg.COLUMNS, t)) for t in self.cg.write_index(rows, failed)]
        return {(r["harness"], r["id"]): r for r in out}

    def test_turn_reads_every_harness_shape(self):
        st = self.cg.step
        self.assertEqual(self.cg.turn([st("assistant", [{"type": "tool_use"}]),
                                       st("user", "fix it")]), (True, "fix it"))
        self.assertEqual(self.cg.turn([st("assistant", [{"type": "text", "text": "done"}]),
                                       st("user", "fix it")]), (False, "fix it"))
        self.assertEqual(self.cg.turn([st("model", [{"functionCall": {}}])])[0], True)
        self.assertEqual(self.cg.turn([st("user", [{"functionResponse": {}}])])[0], True)
        self.assertEqual(self.cg.turn([st("assistant", [], "aborted")])[0], False)
        self.assertEqual(self.cg.turn([st("assistant", [], "error")])[0], True)
        self.assertEqual(self.cg.turn([st("user", "[Request interrupted by user]"),
                                       st("user", "go")]), (False, "go"))
        self.assertIsNone(st("user", "<command-name>/exit</command-name>"))

    def test_history_flags_what_was_cut_off_or_left_mid_turn(self):
        t = time.time() - 3600
        call = {"type": "assistant", "message": {"role": "assistant",
                                                 "content": [{"type": "tool_use"}]}}
        self.transcript("closed", [prompt("one"), REPLY, exit_marker("prompt_input_exit")], t - 600)
        self.transcript("nomark", [prompt("two"), call], t)   # newer than the first marker
        self.transcript("hup", [prompt("three"), REPLY, exit_marker("other")], t)
        self.transcript("sdk", [prompt("four", entry="sdk-cli"), call], t)
        jdir = self.home / ".jcode" / "sessions"
        jdir.mkdir(parents=True)
        (jdir / "session_x.json").write_text(json.dumps({
            "id": "jx", "working_dir": "/j", "status": {"Crashed": {"message": "exited"}},
            "messages": [{"role": "user", "content": [{"type": "text", "text": "build"}]}]}))
        rows = self.history_rows()
        status = lambda k: (rows[k]["status"], rows[k]["how"])
        self.assertEqual(status(("claude", "closed")), ("closed", "exit record: prompt_input_exit"))
        self.assertEqual(status(("claude", "nomark")), ("cut off", "no end record"))
        self.assertEqual(status(("claude", "hup")), ("cut off", "window closed"))
        self.assertEqual(status(("jcode", "jx")), ("cut off", "status Crashed: exited"))
        self.assertEqual((rows[("claude", "nomark")]["mid_turn"],
                          rows[("claude", "nomark")]["last_prompt"]), (1, "two"))
        self.assertEqual(rows[("claude", "nomark")]["resume"], "cd /w && claude --resume nomark")
        self.assertEqual(rows[("claude", "sdk")]["interactive"], 0)
        con = sqlite3.connect(str(self.cg.db_path()))
        audit = {r[0] for r in con.execute("SELECT id FROM audit")}
        con.close()
        self.assertEqual(audit, {"nomark", "hup", "jx"})
        self.assertEqual(os.stat(self.cg.db_path()).st_mode & 0o777, 0o600)

    def test_history_keeps_a_session_its_harness_deleted(self):
        self.transcript("gone", [prompt("hi"), REPLY, exit_marker("other")], time.time() - 60)
        self.history_rows()
        (self.home / ".claude" / "projects" / "-w" / "gone.jsonl").unlink()
        self.transcript("other", [prompt("x"), REPLY], time.time() - 30)
        r = self.history_rows()[("claude", "gone")]
        self.assertEqual((r["in_store"], r["status"], r["title"]), (0, "cut off", "hi"))

    # -- reopening -----------------------------------------------------------

    def test_run_argv_carries_the_typed_line_and_keeps_a_shell_open(self):
        s = self.cg.session("codex", "t", "/a b", 0, "", "")
        s["typed"] = "codex -m y"
        argv = self.cg.run_argv(s)
        self.assertEqual(argv[1], "-lic")
        self.assertIn("cd '/a b' && { export CGR_LAUNCH='codex -m y'; codex -m y resume t; }",
                      argv[2])
        self.assertTrue(argv[2].endswith(" -l"))


if __name__ == "__main__":
    unittest.main()
