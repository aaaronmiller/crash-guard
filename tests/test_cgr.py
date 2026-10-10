"""cgr finds sessions from the harnesses' own records and from its process
watcher, and reopens them with the command you typed -- one test per rule."""
import importlib.machinery
import importlib.util
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
