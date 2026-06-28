import argparse
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


class RestoreSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cg = load_crash_guard()

    def args(self, **overrides):
        values = {
            "from_archive": False,
            "group": 0,
            "yes": True,
            "dry_run": False,
            "item": "",
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_yes_without_explicit_group_selects_all_visible_groups(self):
        groups = [
            {"records": [{"inv_id": "one"}]},
            {"records": [{"inv_id": "two"}, {"inv_id": "three"}]},
        ]

        records = self.cg.select_group_records(groups, self.args())

        self.assertEqual(["one", "two", "three"], [r["inv_id"] for r in records])

    def test_explicit_group_keeps_group_scoped_restore(self):
        groups = [
            {"records": [{"inv_id": "one"}]},
            {"records": [{"inv_id": "two"}, {"inv_id": "three"}]},
        ]

        records = self.cg.select_group_records(groups, self.args(group=2))

        self.assertEqual(["two", "three"], [r["inv_id"] for r in records])

    def test_single_dash_long_flag_duplicate_is_not_appended(self):
        recorded = ["claude", "-dangerously-skip-permissions"]
        restore = ["rtk", "claude", "-r", "session-id", "--dangerously-skip-permissions"]

        extra = self.cg.argv_extra_args(recorded, restore, "claude")

        self.assertEqual([], extra)

    def test_hermes_recorded_under_rtx_displays_as_hermes(self):
        record = {"key": "rtx", "argv": ["hermes"], "cwd": "/tmp/project"}

        display = self.cg._resolve_display_key(record, self.cg.DEFAULT_CONFIG)
        key, _ = self.cg.resolve_record_program(record, self.cg.DEFAULT_CONFIG)

        self.assertEqual("hermes", display)
        self.assertEqual("hermes", key)

    def test_hermes_wrapped_with_rtk_displays_as_hermes(self):
        record = {"key": "hermes", "argv": ["rtk", "hermes", "--continue"], "cwd": "/tmp/project"}

        display = self.cg._resolve_display_key(record, self.cg.DEFAULT_CONFIG)
        key, _ = self.cg.resolve_record_program(record, self.cg.DEFAULT_CONFIG)

        self.assertEqual("hermes", display)
        self.assertEqual("hermes", key)

    def test_restore_group_payload_saves_clean_records(self):
        selected = [
            {"rec": {"inv_id": "one", "key": "claude", "cwd": "/tmp/a", "_path": "/live/one.json"}},
            {"rec": {"inv_id": "two", "key": "pi", "cwd": "/tmp/b", "_history_status": "crashed"}},
        ]

        payload = self.cg.restore_group_payload("restore-1", selected, "restore")

        self.assertEqual("restore_group", payload["event"])
        self.assertEqual("restore-1", payload["restore_id"])
        self.assertEqual(["one", "two"], [r["inv_id"] for r in payload["records"]])
        self.assertFalse(any("_path" in r for r in payload["records"]))
        self.assertFalse(any("_history_status" in r for r in payload["records"]))

    def test_archive_restore_groups_uses_saved_restore_group_records(self):
        original_read_history = self.cg.read_history_events
        original_read_archived = self.cg.read_archived_sentinels
        try:
            self.cg.read_history_events = lambda: [
                {
                    "event": "restore_group",
                    "at": "2026-06-28T02:00:00-0700",
                    "restore_id": "restore-1",
                    "records": [
                        {"inv_id": "one", "key": "claude", "cwd": "/tmp/a", "argv": ["claude"]},
                        {"inv_id": "two", "key": "pi", "cwd": "/tmp/b", "argv": ["pi"]},
                    ],
                }
            ]
            self.cg.read_archived_sentinels = lambda: [
                {"inv_id": "one", "key": "claude", "cwd": "/tmp/a", "argv": ["claude"], "_path": "/archive/one.json"},
                {"inv_id": "two", "key": "pi", "cwd": "/tmp/b", "argv": ["pi"], "_path": "/archive/two.json"},
            ]

            groups = self.cg.archive_restore_groups()

            self.assertEqual(1, len(groups))
            self.assertEqual("archived", groups[0]["status"])
            self.assertEqual("restore-1", groups[0]["_saved_group_id"])
            self.assertEqual(["one", "two"], [r["inv_id"] for r in groups[0]["records"]])
            self.assertEqual(["/archive/one.json", "/archive/two.json"], [r["_path"] for r in groups[0]["records"]])
        finally:
            self.cg.read_history_events = original_read_history
            self.cg.read_archived_sentinels = original_read_archived

    def test_archive_restore_groups_reconstructs_historical_restore_start(self):
        original_read_history = self.cg.read_history_events
        original_read_archived = self.cg.read_archived_sentinels
        try:
            self.cg.read_history_events = lambda: [
                {
                    "event": "restore_start",
                    "at": "2026-06-28T02:00:00-0700",
                    "restore_id": "restore-legacy",
                    "plan": [
                        {"inv_id": "one", "key": "claude", "cwd": "/tmp/a"},
                        {"inv_id": "two", "key": "pi", "cwd": "/tmp/b"},
                    ],
                }
            ]
            self.cg.read_archived_sentinels = lambda: [
                {"inv_id": "one", "key": "claude", "cwd": "/tmp/a", "argv": ["claude"], "_path": "/archive/one.json"},
                {"inv_id": "two", "key": "pi", "cwd": "/tmp/b", "argv": ["pi"], "_path": "/archive/two.json"},
            ]

            groups = self.cg.archive_restore_groups()

            self.assertEqual(1, len(groups))
            self.assertEqual("restore-legacy", groups[0]["_saved_group_id"])
            self.assertEqual(["one", "two"], [r["inv_id"] for r in groups[0]["records"]])
        finally:
            self.cg.read_history_events = original_read_history
            self.cg.read_archived_sentinels = original_read_archived

    def test_archive_yes_without_explicit_group_selects_newest_saved_group(self):
        groups = [
            {"records": [{"inv_id": "new-one"}, {"inv_id": "new-two"}]},
            {"records": [{"inv_id": "old-one"}]},
        ]

        records = self.cg.select_group_records(
            groups,
            self.args(from_archive=True),
            default_all=False,
            include_all_option=False,
        )

        self.assertEqual(["new-one", "new-two"], [r["inv_id"] for r in records])


if __name__ == "__main__":
    unittest.main()
