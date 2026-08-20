"""Regression test for the leaked `rec` loop variable in history_sessions().

The ephemeral filter is meant to hide sessions that started and immediately
exited. It has three checks: duration, payload size, and turn count. The
duration check reads the session under evaluation. The payload and turn-count
checks did not.

In `history_sessions()` the ephemeral pass iterates:

    for inv_id, cur in sessions.items():
        ...
        tool_key, _ = resolve_record_program(cur.get("record", {}), cfg)   # correct
        ...
        payload = _find_session_payload(rec, tool_key)                     # WRONG

`rec` is not the session under evaluation. It is whatever `rec` was last bound
to by the earlier `for rec in live:` loop, so every session's payload check
inspected the last live sentinel instead of its own. If there were no live
sentinels at all, `rec` was never bound and the reference raised NameError.

This test pins the intended behaviour: the payload lookup must receive the
record of the session being evaluated.
"""
import importlib.machinery
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin" / "crash-guard"


def load_crash_guard():
    loader = importlib.machinery.SourceFileLoader("crash_guard", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class EphemeralPayloadScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cg = load_crash_guard()

    def test_payload_lookup_receives_the_evaluated_session_record(self):
        """Each session's payload check must use that session's own record.

        Two sessions are fed through the ephemeral pass. We record which record
        `_find_session_payload` is asked about. If the scoping is correct we see
        each session's own inv_id exactly once. Under the bug we see the same
        record twice, or a NameError.
        """
        cg = self.cg
        seen = []

        def fake_find_session_payload(record, tool_key=None):
            seen.append((record or {}).get("inv_id"))
            return None

        def fake_read_history_events():
            return [
                {
                    "event": "stop",
                    "at": "2026-08-20T10:00:10-0700",
                    "duration_secs": 3600,
                    "record": {
                        "inv_id": "alpha",
                        "key": "claude",
                        "cwd": "/home/cheta/code/alpha",
                        "argv": ["claude"],
                        "launched_at": "2026-08-20T09:00:10-0700",
                        "boot_id": "boot-1",
                        "shell_pid": 111,
                    },
                },
                {
                    "event": "stop",
                    "at": "2026-08-20T10:00:20-0700",
                    "duration_secs": 7200,
                    "record": {
                        "inv_id": "beta",
                        "key": "codex",
                        "cwd": "/home/cheta/code/beta",
                        "argv": ["codex"],
                        "launched_at": "2026-08-20T08:00:20-0700",
                        "boot_id": "boot-1",
                        "shell_pid": 222,
                    },
                },
            ]

        originals = {
            "read_history_events": cg.read_history_events,
            "read_archived_sentinels": cg.read_archived_sentinels,
            "read_sentinels": cg.read_sentinels,
            "_find_session_payload": cg._find_session_payload,
            "load_config": cg.load_config,
        }
        cg.read_history_events = fake_read_history_events
        cg.read_archived_sentinels = lambda: []
        # No live sentinels. This is the case that raised NameError under the bug.
        cg.read_sentinels = lambda: []
        cg._find_session_payload = fake_find_session_payload
        cg.load_config = lambda: {
            "ephemeral": {
                "max_duration_secs": 5,
                "max_payload_bytes": 500,
                "min_user_turns": 3,
            },
            "programs": {},
        }
        try:
            sessions = cg.history_sessions()
        finally:
            for name, fn in originals.items():
                setattr(cg, name, fn)

        self.assertEqual(set(sessions), {"alpha", "beta"})

        # Both sessions ran far longer than the 5s threshold, so neither is
        # ephemeral by duration and both reach the payload checks. There are two
        # such checks per session, the payload-size one and the turn-count one,
        # so each inv_id must appear exactly twice.
        #
        # Under the bug this list was ['beta', 'beta', 'beta', 'beta']: one
        # record answered for every session.
        self.assertEqual(
            sorted(seen),
            ["alpha", "alpha", "beta", "beta"],
            "each session's payload check must use its own record; "
            f"got {seen!r}",
        )
        self.assertNotIn(
            None, seen, "a payload check ran against an empty record"
        )


if __name__ == "__main__":
    unittest.main()
