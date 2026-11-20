#!/usr/bin/env python3
"""RAPLH loop guard: run the regression suite and stop the loop on repeated failure.

Playbook section 04: "If the same fix fails twice, stop the loop and escalate to a mentor."

  python .kiro/scripts/loop_guard.py            run tests, record the attempt
  python .kiro/scripts/loop_guard.py --status   show the loop state
  python .kiro/scripts/loop_guard.py --reset    HUMAN ONLY: clear an escalation

Escalates when the identical failure signature appears on MAX_REPEATS + 1 consecutive runs
(the original failure plus two fixes that changed nothing), or after MAX_FAILED_RUNS failed runs
since the last pass. While escalated, pre_tool_guard.py blocks all file changes by the agent.
Exit codes: 0 pass (or no tests yet), 1 fail, 3 escalated.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".kiro" / "state" / "loop_state.json"
SAST = timezone(timedelta(hours=2))
MAX_REPEATS = 2
MAX_FAILED_RUNS = 5


def now() -> str:
    return datetime.now(SAST).isoformat(timespec="seconds")


def load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"escalated": False, "repeat_count": 0, "failed_runs": 0, "last_signature": [], "history": []}


def save(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    state["history"] = state.get("history", [])[-20:]
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE)


def signature(output: str) -> list[str]:
    lines = [ln.strip()[:200] for ln in output.splitlines() if re.match(r"^(FAILED|ERROR)\s", ln.strip())]
    return sorted(set(lines))


def project_python() -> str:
    """Prefer the project's .venv interpreter so tests use the pinned dependencies."""
    for candidate in (ROOT / ".venv" / "Scripts" / "python.exe", ROOT / ".venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    state = load()

    if args.status:
        print(json.dumps({k: v for k, v in state.items() if k != "history"}, indent=2))
        return 3 if state.get("escalated") else 0

    if args.reset:
        if sys.stdin.isatty() and input("Human reset of the RAPLH loop. Type RESET to confirm: ").strip() != "RESET":
            print("Not reset.")
            return 1
        state.update(escalated=False, repeat_count=0, failed_runs=0, last_signature=[])
        state.setdefault("history", []).append({"time": now(), "event": "human_reset"})
        save(state)
        print("Loop guard reset by human.")
        return 0

    if state.get("escalated"):
        print("ESCALATE: loop already stopped. Hand over to the human/mentor with the failure below.")
        print("\n".join(state.get("last_signature", [])))
        return 3

    try:
        proc = subprocess.run([project_python(), "-m", "pytest", "-q", "-rfE", "--tb=short", "-p", "no:cacheprovider"],
                              cwd=ROOT, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        proc = subprocess.CompletedProcess(args=[], returncode=1, stdout="FAILED pytest run timed out after 300 s", stderr="")
    output = (proc.stdout or "") + (proc.stderr or "")
    tail = "\n".join(output.strip().splitlines()[-40:])

    if proc.returncode == 5:
        print("NO TESTS COLLECTED yet (not counted as an attempt).")
        return 0
    if proc.returncode == 0:
        state.update(repeat_count=0, failed_runs=0, last_signature=[])
        state.setdefault("history", []).append({"time": now(), "result": "pass"})
        save(state)
        print(tail)
        print("\nPASS: loop counters cleared.")
        return 0

    sig = signature(output) or [f"pytest exit code {proc.returncode}"]
    state["failed_runs"] = state.get("failed_runs", 0) + 1
    state["repeat_count"] = state.get("repeat_count", 0) + 1 if sig == state.get("last_signature") else 0
    state["last_signature"] = sig
    state["escalated"] = state["repeat_count"] >= MAX_REPEATS or state["failed_runs"] >= MAX_FAILED_RUNS
    state.setdefault("history", []).append({"time": now(), "result": "fail", "failures": sig})
    save(state)

    print(tail)
    print(f"\nFAIL: attempt {state['failed_runs']}/{MAX_FAILED_RUNS}, "
          f"same failure repeated {state['repeat_count']}/{MAX_REPEATS} times.")
    if state["escalated"]:
        print("ESCALATE: stop the loop. Do not attempt another fix. Present the failure, what was tried, "
              "and your diagnosis to the human/mentor. File changes are now locked by pre-tool-guard.")
        return 3
    print("Read the failure and find the cause before the next change.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
