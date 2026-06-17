#!/usr/bin/env python3
"""
Fix crash-guard sentinels and restore all sessions.

The rtk launcher incorrectly writes key="rtk" and truncated argv.
This script fixes the live sentinels to have correct keys and full argv,
then restores all sessions.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

LIVE_DIR = Path.home() / ".local/share/crash-guard/live"
ARCH_DIR = Path.home() / ".local/share/crash-guard/archive"

# Program configurations from programs.json
PROGRAM_CONFIGS = {
    "pi": {
        "key": "pi",
        "argv_template": ["rtk", "pi", "--provider", "openai", "-c"],
        "continue_argv": ["rtk", "pi", "--provider", "openai", "-c"],
    },
    "hermes": {
        "key": "hermes",
        "argv_template": ["rtk", "hermes", "--continue", "--dangerously-skip-permissions"],
        "continue_argv": ["rtk", "hermes", "--continue", "--dangerously-skip-permissions"],
    },
    "codex": {
        "key": "codex",
        "argv_template": ["rtk", "codex", "resume", "--last", "--dangerously-bypass-approvals-and-sandbox"],
        "continue_argv": ["rtk", "codex", "resume", "--last", "--dangerously-bypass-approvals-and-sandbox"],
    },
}

def load_sentinel(path):
    with open(path) as f:
        return json.load(f)

def save_sentinel(path, data):
    # Backup first
    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    with open(path, "w") as f:
        json.dump(data, f)
        f.write("\n")

def fix_sentinel(rec):
    """Fix a sentinel record to have correct key and full argv."""
    argv = rec.get("argv", [])
    if not argv:
        return rec
    
    # The first argv element is the actual program
    program = argv[0] if argv else None
    if program not in PROGRAM_CONFIGS:
        print(f"  Warning: Unknown program '{program}' in argv: {argv}")
        return rec
    
    config = PROGRAM_CONFIGS[program]
    rec["key"] = config["key"]
    rec["argv"] = config["argv_template"]
    return rec

def main():
    print("=" * 60)
    print("Fixing crash-guard live sentinels")
    print("=" * 60)
    
    sentinel_files = sorted(LIVE_DIR.glob("*.json"))
    if not sentinel_files:
        print("No live sentinels found.")
        return
    
    print(f"Found {len(sentinel_files)} live sentinel(s)")
    print()
    
    # Fix each sentinel
    for sf in sentinel_files:
        rec = load_sentinel(sf)
        old_key = rec.get("key")
        old_argv = rec.get("argv")
        
        fixed = fix_sentinel(rec)
        new_key = fixed.get("key")
        new_argv = fixed.get("argv")
        
        if old_key != new_key or old_argv != new_argv:
            print(f"Fixing: {sf.name}")
            print(f"  key: {old_key} -> {new_key}")
            print(f"  argv: {old_argv} -> {new_argv}")
            save_sentinel(sf, fixed)
        else:
            print(f"OK: {sf.name} (key={old_key})")
    
    print()
    print("=" * 60)
    print("Running crash-guard restore --list-groups")
    print("=" * 60)
    
    # Show the restore plan
    result = subprocess.run(
        ["/home/cheta/code/crash-guard/bin/crash-guard", "restore", "--list-groups"],
        capture_output=True, text=True
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr)
    
    print()
    print("=" * 60)
    print("To restore all sessions, run:")
    print("  crash-guard restore --group 1 --yes")
    print("  crash-guard restore --group 2 --yes")
    print("  crash-guard restore --group 3 --yes  # archived")
    print("=" * 60)

if __name__ == "__main__":
    main()