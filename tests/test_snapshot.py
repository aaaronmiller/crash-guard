"""Tests for cg-snapshot, the liveness grader.

The behaviours worth pinning are the ones whose absence caused the failures
this tool exists to measure:

  * rotation is enforced, because the design it replaces failed as an unbounded
    store that grew to 13,598 records unnoticed;
  * the crash set is the last snapshot from a FOREIGN boot, not merely the last
    snapshot, because "different boot" is the only positive evidence available;
  * helper subprocesses and headless runs are excluded, because a restore that
    reopens a daemon as a tab is noise;
  * files are owner-only, because snapshots record every working directory.
"""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin" / "cg-snapshot"


def load():
    loader = importlib.machinery.SourceFileLoader("cg_snapshot", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.cg = load()
        self.tmp = tempfile.TemporaryDirectory()
        self.cg.STATE = Path(self.tmp.name)
        self.cg.SNAP_DIR = self.cg.STATE / "snapshots"

    def tearDown(self):
        self.tmp.cleanup()

    def _snap(self, epoch, boot, sessions=()):
        return {"at": "x", "epoch": epoch, "boot_id": boot,
                "sessions": list(sessions)}

    def test_written_snapshot_is_owner_only(self):
        p = self.cg.write_snapshot(self._snap(time.time(), "boot-1"))
        self.assertEqual(p.stat().st_mode & 0o777, 0o600,
                         "snapshots record every cwd and must not be readable by others")
        self.assertEqual(self.cg.SNAP_DIR.stat().st_mode & 0o777, 0o700)

    def test_rotation_enforces_the_count_bound(self):
        self.cg.MAX_SNAPSHOTS = 5
        for i in range(12):
            self.cg.write_snapshot(self._snap(1000 + i, "boot-1"))
        removed = self.cg.rotate()
        remaining = list(self.cg.SNAP_DIR.glob("*.json"))
        self.assertEqual(len(remaining), 5, f"expected 5 kept, removed {removed}")

    def test_rotation_enforces_the_age_bound(self):
        self.cg.MAX_AGE_DAYS = 1
        old = self.cg.write_snapshot(self._snap(1000, "boot-1"))
        stale = time.time() - 3 * 86400
        os.utime(old, (stale, stale))
        fresh = self.cg.write_snapshot(self._snap(time.time(), "boot-1"))
        self.cg.rotate()
        self.assertFalse(old.exists(), "snapshot older than the age bound survived")
        self.assertTrue(fresh.exists(), "fresh snapshot was rotated out")

    def test_crash_set_is_the_last_snapshot_from_a_previous_boot(self):
        """Not simply the newest snapshot."""
        self.cg.write_snapshot(self._snap(1000, "boot-OLD",
                                          [{"pid": 1, "harness": "omp",
                                            "cwd": "/a", "argv": "omp"}]))
        self.cg.write_snapshot(self._snap(2000, "boot-OLD",
                                          [{"pid": 2, "harness": "claude",
                                            "cwd": "/b", "argv": "claude"}]))
        # current boot; these must be ignored
        self.cg.write_snapshot(self._snap(3000, "boot-NEW",
                                          [{"pid": 3, "harness": "muse",
                                            "cwd": "/c", "argv": "muse"}]))
        self.cg.boot_id = lambda: "boot-NEW"

        got = self.cg.crash_set()
        self.assertIsNotNone(got, "no crash set returned")
        self.assertEqual(got["boot_id"], "boot-OLD")
        self.assertEqual([s["pid"] for s in got["sessions"]], [2],
                         "must be the LAST pre-boot snapshot, not the first")

    def test_crash_set_is_none_when_every_snapshot_is_this_boot(self):
        self.cg.write_snapshot(self._snap(1000, "boot-NEW"))
        self.cg.boot_id = lambda: "boot-NEW"
        self.assertIsNone(self.cg.crash_set(),
                          "a clean boot with no prior snapshots has no crash set")

    def test_helper_and_headless_patterns_are_excluded(self):
        excluded = [
            "/home/u/.local/bin/muse-bin session-message serve --socket /tmp/x",
            "/home/u/.local/bin/muse-bin __tbh_internal_process_owner_host_v1 31 1",
            "/home/u/.claude/security/agent-sdk-venv/lib/python3.12/site-packages/claude/x.py",
            "omp -p --model foo 'do a thing'",
            "claude --version",
        ]
        for cmd in excluded:
            with self.subTest(cmd=cmd[:40]):
                self.assertTrue(
                    self.cg.NOT_A_SESSION.search(cmd) or self.cg.HELPER.search(cmd),
                    f"should have been excluded: {cmd}",
                )

    def test_real_sessions_are_not_excluded(self):
        kept = [
            "claude --dangerously-skip-permissions",
            "bun /home/u/.bun/bin/omp --resume",
            "/home/u/.local/bin/muse-bin-0.2.1 --yolo",
            "codex resume",
        ]
        for cmd in kept:
            with self.subTest(cmd=cmd[:40]):
                self.assertFalse(
                    self.cg.NOT_A_SESSION.search(cmd) or self.cg.HELPER.search(cmd),
                    f"real session wrongly excluded: {cmd}",
                )
                self.assertTrue(
                    any(p.search(cmd) for _, p in self.cg.HARNESS_PATTERNS),
                    f"no harness matched: {cmd}",
                )


if __name__ == "__main__":
    unittest.main()
