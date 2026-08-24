"""Retention rules for the sentinel store.

Two defects motivated these tests, both observed on a real store that had grown
to 13,608 sentinels:

  * `prune` could only remove `stale` records -- this boot's, with a dead shell.
    Records from an earlier boot are `crashed`, which is the class a restore
    consumes, so nothing in the tool could ever remove one. They accumulated
    from June to August until `status` printed thousands of lines and no real
    session could be found in it.

  * A sentinel was written for every invocation, including probes. One loop
    running `pi --version` on 2026-07-18 left 11,984 records, 88% of the store.
    Such an invocation exits immediately and has nothing to reattach to, so the
    sentinel could never serve its purpose.

The default behaviour of `prune` must stay conservative: a bare run still
removes only `stale`, because a crashed record is exactly what the user needs
after a reboot.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_loader(
    "crash_guard",
    importlib.machinery.SourceFileLoader(
        "crash_guard", str(Path(__file__).resolve().parent.parent / "bin" / "crash-guard")
    ),
)
cg = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cg)


class Args:
    def __init__(self, **kw):
        self.older_than = None
        self.trivial = False
        self.all_crashed = False
        self.dry_run = False
        for k, v in kw.items():
            setattr(self, k, v)


def write_sentinel(live, inv_id, *, boot, argv, launched, pid=999999):
    rec = {
        "inv_id": inv_id,
        "key": argv[0],
        "cwd": "/tmp",
        "argv": argv,
        "boot_id": boot,
        "shell_pid": pid,
        "launched_at": launched,
        "host": "test",
    }
    (live / (inv_id + ".json")).write_text(json.dumps(rec) + "\n")
    return rec


@pytest.fixture
def store(tmp_path, monkeypatch):
    live = tmp_path / "live"
    live.mkdir()
    monkeypatch.setattr(cg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cg, "LIVE_DIR", live)
    monkeypatch.setattr(cg, "boot_id", lambda: "CURRENT-BOOT")
    return live


def test_probe_invocations_are_recognised():
    assert cg.is_trivial_invocation({"argv": ["pi", "--version"]})
    assert cg.is_trivial_invocation({"argv": ["command", "pi", "--version"]})
    assert cg.is_trivial_invocation({"argv": ["claude", "--help"]})
    # A real session, and the degenerate single-token case, are not probes.
    assert not cg.is_trivial_invocation({"argv": ["claude", "--dangerously-skip-permissions"]})
    assert not cg.is_trivial_invocation({"argv": ["pi"]})
    assert not cg.is_trivial_invocation({"argv": []})


def test_bare_prune_keeps_crashed_records(store, capsys):
    """The conservative default: a crashed record survives, because it is
    exactly what `restore` needs after a reboot."""
    write_sentinel(store, "old", boot="PREVIOUS-BOOT", argv=["claude"],
                   launched="2026-06-01T10:00:00-0700")
    cg.cmd_prune(Args())
    assert (store / "old.json").exists()


def test_trivial_flag_removes_probes_only(store):
    write_sentinel(store, "probe", boot="PREVIOUS-BOOT", argv=["pi", "--version"],
                   launched="2026-07-18T01:00:00-0700")
    write_sentinel(store, "real", boot="PREVIOUS-BOOT", argv=["claude"],
                   launched="2026-07-18T01:00:00-0700")
    cg.cmd_prune(Args(trivial=True))
    assert not (store / "probe.json").exists()
    assert (store / "real.json").exists(), "a real crashed session must survive --trivial"


def test_older_than_respects_the_cutoff(store):
    import datetime as dt
    recent = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S%z")
    ancient = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%S%z")
    write_sentinel(store, "recent", boot="PREVIOUS-BOOT", argv=["claude"], launched=recent)
    write_sentinel(store, "ancient", boot="PREVIOUS-BOOT", argv=["claude"], launched=ancient)
    cg.cmd_prune(Args(older_than=14))
    assert (store / "recent.json").exists(), "a 2-day-old crash is still restorable"
    assert not (store / "ancient.json").exists()


def test_dry_run_deletes_nothing(store):
    write_sentinel(store, "probe", boot="PREVIOUS-BOOT", argv=["pi", "--version"],
                   launched="2026-07-18T01:00:00-0700")
    cg.cmd_prune(Args(trivial=True, dry_run=True))
    assert (store / "probe.json").exists()
