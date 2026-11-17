"""Targeted tests for the forensic detector defects found by grading 2026-08-29.

Each case pins a defect grading surfaced against the 2026-08-20 boot:
- muse `recorded_at` is an unquoted epoch-microseconds integer, which the
  content scanner could never parse, so muse sessions produced no
  record-in-window signal.
- The marker patterns for omp/claude/agy pointed at directories that do not
  exist, so the startup-marker signal never fired for them.
- The omp session key was the slug directory, which no marker carries, so even
  a correct marker path could not match.
- Grading derived the crash moment from log mtimes, which points at a later
  boot once the machine has rebooted since the crash.
"""

import importlib.util
import json
import re
import time
from pathlib import Path
from importlib.machinery import SourceFileLoader

import pytest
_loader = SourceFileLoader("cg_forensic",
                           str(Path(__file__).resolve().parent.parent / "bin" / "cg-forensic"))
SPEC = importlib.util.spec_from_loader("cg_forensic", _loader)
cg = importlib.util.module_from_spec(SPEC)
import sys
sys.modules["cg_forensic"] = cg
_loader.exec_module(cg)

def test_muse_epoch_microseconds_in_window(tmp_path):
    """A muse record with an unquoted recorded_at integer must count as a
    crash-window write. Regression: the ISO-only regex returned None here."""
    p = tmp_path / "session.jsonl"
    now = time.time()
    p.write_text(json.dumps({
        "payload_type": "runtime.session.route_facts",
        "recorded_at": int(now * 1e6),
        "payload": {"record": {"cwd": "/tmp/x"}},
    }) + "\n")
    got = cg.record_in_window(p, now - 15, now + 15)
    assert got is not None
    assert abs(got - now) < 15


def test_muse_epoch_microseconds_outside_window(tmp_path):
    p = tmp_path / "session.jsonl"
    now = time.time()
    p.write_text('{"recorded_at":%d}\n' % int((now - 3600) * 1e6))
    assert cg.record_in_window(p, now - 15, now + 15) is None


def test_omp_key_is_session_uuid_not_slug():
    """The omp key regex must capture the uuid in the filename; the slug
    directory matches no marker. Regression: key was `-code-quartermaster`.
    Real filename shape: 2026-08-11T15-45-46-077Z_019ff180-....jsonl (the
    uuid only; the `<pid>-<uuid>` form seen in markers is the client id)."""
    entry = next(e for e in cg.SESSION_LOGS if e[0] == "omp")
    m = re.search(
        entry[2],
        "/home/cheta/.omp/agent/sessions/-code-quartermaster/"
        "2026-08-11T15-45-46-077Z_019ff180-505d-7000-9ab1-5186a6490160.jsonl")
    assert m and m.group(1) == "019ff180-505d-7000-9ab1-5186a6490160"


# The globs bin/cg-forensic uses for signal 3, each with a file of the shape its
# harness really writes (observed 2026-08-29; omp names its client <pid>-<uuid>).
MARKERS = {
    ".omp/run/daemons/*/clients/*.json":
        ".omp/run/daemons/685ffa17f648f081/clients/4242-019ff180-505d-7000-9ab1-5186a6490160.json",
    ".claude/security_warnings_state_*.json":
        ".claude/security_warnings_state_8f6e2c1a-5b7d-4e0a-9c3b-2d1f0a9b8c7d.json",
    ".gemini/antigravity-cli/presence/*.lock":
        ".gemini/antigravity-cli/presence/c686714d-4737-4b6a-afd9-9d9a0a272a73.lock",
}


def test_marker_patterns_match_the_files_each_harness_writes(tmp_path, monkeypatch):
    """Every marker glob must match the file its harness writes, or the marker
    signal is dead for that harness. Regression: three of four original
    patterns matched nothing anywhere.

    Hermetic: the files are made here. A marker exists only while its harness
    runs (omp's client file goes with the client), so reading this machine's
    home made the test pass or fail with whatever happened to be open."""
    monkeypatch.setattr(cg, "HOME", tmp_path)
    for pattern, example in MARKERS.items():
        assert not any(tmp_path.glob(pattern)), pattern
        marker = tmp_path / example
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"projectDir": "/work/here"}))
        assert list(tmp_path.glob(pattern)) == [marker], pattern
    # and the code that reads omp's marker finds it by the uuid in its name
    assert cg.cwd_from_omp_marker("019ff180-505d-7000-9ab1-5186a6490160") == "/work/here"


def test_marker_patterns_match_real_files_where_there_are_any():
    """The same globs against this machine's home, as far as it can say: a
    marker exists only while its harness runs, so a glob with no file is
    unverified here, not wrong. Skipped when nothing is open to check."""
    unverified = [p for p in MARKERS if not any(cg.HOME.glob(p))]
    if len(unverified) == len(MARKERS):
        pytest.skip("no harness has a marker open on this machine")


def test_cwd_from_muse_reads_route_facts(tmp_path):
    p = tmp_path / "session.jsonl"
    p.write_text(json.dumps({
        "payload_type": "runtime.session.route_facts",
        "payload": {"record": {"cwd": "/home/cheta/code/living-documents"}},
    }) + "\n")
    assert cg.cwd_from_muse(p) == "/home/cheta/code/living-documents"


def test_cwd_from_muse_falls_back_to_workspace_root(tmp_path):
    p = tmp_path / "session.jsonl"
    p.write_text(json.dumps({
        "payload_type": "runtime.session.metadata",
        "payload": {"record": {"workspace_root": "/home/cheta/code/living-documents"}},
    }) + "\n")
    assert cg.cwd_from_muse(p) == "/home/cheta/code/living-documents"


def test_snapshot_crash_moment_ignores_current_boot(tmp_path, monkeypatch):
    """The grading crash moment is the last snapshot of a PRIOR boot; snapshots
    from the current boot must not move it."""
    monkeypatch.setattr(cg, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(cg, "current_boot_id", lambda: "running")
    (tmp_path / "1000.json").write_text(json.dumps({"epoch": 1000.0, "boot_id": "old"}))
    (tmp_path / "2000.json").write_text(json.dumps({"epoch": 2000.0, "boot_id": "old"}))
    (tmp_path / "9000.json").write_text(json.dumps({"epoch": 9000.0, "boot_id": "running"}))
    assert cg.snapshot_crash_moment() == 2000.0


def test_snapshot_crash_moment_none_without_prior_boot(tmp_path, monkeypatch):
    monkeypatch.setattr(cg, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(cg, "current_boot_id", lambda: "running")
    (tmp_path / "9000.json").write_text(json.dumps({"epoch": 9000.0, "boot_id": "running"}))
    assert cg.snapshot_crash_moment() is None


def test_snapshot_crash_moment_tolerates_corrupt_files(tmp_path, monkeypatch):
    monkeypatch.setattr(cg, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(cg, "current_boot_id", lambda: "running")
    (tmp_path / "1.json").write_text("{not json")
    (tmp_path / "2000.json").write_text(json.dumps({"epoch": 2000.0, "boot_id": "old"}))
    assert cg.snapshot_crash_moment() == 2000.0
