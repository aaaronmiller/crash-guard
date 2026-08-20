"""Regression test: the history log and its directories must be owner-only.

crash-guard's own README warns that history is plaintext and records every
working directory and every argv. `atomic_write()` was tightened to 0600 for
sentinels, but two things were missed:

  * `append_history_payload` opens history.jsonl with a plain "a", so on first
    creation it takes the process umask. On this machine that produced 0644,
    world-readable, for a file holding 13,909 records of cwd and argv.
  * `ensure_dirs()` created DATA_DIR, LIVE_DIR and ARCH_DIR with the default
    mkdir mode, which produced 0755, world-traversable.

Verified 2026-08-20 on the live install: history.jsonl was 0644 and all three
directories were 0755, while the newest sentinels were correctly 0600.
"""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin" / "crash-guard"


def load_crash_guard():
    loader = importlib.machinery.SourceFileLoader("crash_guard", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class HistoryPermissionTests(unittest.TestCase):
    def setUp(self):
        self.cg = load_crash_guard()
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        # Repoint every path constant at the sandbox.
        self.cg.CFG_DIR = base / "config"
        self.cg.DATA_DIR = base / "data"
        self.cg.LIVE_DIR = self.cg.DATA_DIR / "live"
        self.cg.ARCH_DIR = self.cg.DATA_DIR / "archive"
        self.cg.HISTORY_FILE = self.cg.DATA_DIR / "history.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def test_history_file_is_owner_only_after_append(self):
        self.cg.append_history("start", rec={"inv_id": "x", "key": "claude"})
        self.assertTrue(self.cg.HISTORY_FILE.exists(), "history was not written")
        mode = self.cg.HISTORY_FILE.stat().st_mode & 0o777
        self.assertEqual(
            mode, 0o600,
            f"history.jsonl is {oct(mode)}; it records cwd and argv and must "
            "not be group or world readable",
        )

    def test_history_file_is_repaired_if_already_loose(self):
        """An install that already has a 0644 history must be tightened."""
        self.cg.ensure_dirs()
        self.cg.HISTORY_FILE.write_text('{"event":"seed"}\n')
        os.chmod(self.cg.HISTORY_FILE, 0o644)
        self.cg.append_history("start", rec={"inv_id": "y", "key": "codex"})
        mode = self.cg.HISTORY_FILE.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600, f"pre-existing loose history stayed {oct(mode)}")

    def test_data_directories_are_owner_only(self):
        self.cg.ensure_dirs()
        for d in (self.cg.DATA_DIR, self.cg.LIVE_DIR, self.cg.ARCH_DIR):
            mode = d.stat().st_mode & 0o777
            self.assertEqual(
                mode, 0o700,
                f"{d} is {oct(mode)}; it holds sentinels naming every cwd",
            )


if __name__ == "__main__":
    unittest.main()
