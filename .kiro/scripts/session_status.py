#!/usr/bin/env python3
"""SessionStart hook: print the deadline, loop state, task scope and secrets status for the agent."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAST = timezone(timedelta(hours=2))
DEADLINE = datetime(2026, 11, 20, 17, 0, tzinfo=SAST)


def git_ok(*args: str) -> bool:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def main() -> None:
    left = DEADLINE - datetime.now(SAST)
    if left.total_seconds() > 0:
        h, m = divmod(int(left.total_seconds() // 60), 60)
        print(f"RAHN assessment: {h} h {m} min until submission (Fri 20 Nov 2026 17:00 SAST).")
    else:
        print("RAHN assessment: the submission deadline has passed.")

    try:
        state = json.loads((ROOT / ".kiro/state/loop_state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if state.get("escalated"):
        print("LOOP ESCALATED: file changes are locked. Hand over to the human; only they can reset.")
    else:
        print(f"Loop guard: OK (failed runs since last pass: {state.get('failed_runs', 0)}).")

    scope_file = ROOT / ".kiro/current_task_files.txt"
    scope = [ln.strip() for ln in scope_file.read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.startswith("#")] if scope_file.exists() else []
    print("Current task scope: " + (", ".join(scope) if scope else "not set (writes will ask for approval)"))

    env = ROOT / ".env"
    if env.exists():
        ignored = git_ok("check-ignore", "-q", ".env")
        tracked = git_ok("ls-files", "--cached", "--error-unmatch", ".env")
        print(f".env present; git-ignored={ignored}; tracked={tracked}." + (" FIX BEFORE COMMIT." if tracked or not ignored else ""))
    else:
        print(".env not present (the human creates it from .env.example).")
    print("Rules: no invented data; human approval before any external action; never touch .env or the "
          "README 'Working style' section; run `python .kiro/scripts/loop_guard.py` after each change set.")


if __name__ == "__main__":
    main()
