"""Self-test for pre_tool_guard.py: pipes simulated Kiro PreToolUse payloads into the guard.

Run: python .kiro/scripts/guard_selftest.py
Temporarily creates .kiro/current_task_files.txt and .kiro/state/loop_state.json and restores them afterwards.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / ".kiro/scripts/pre_tool_guard.py"
SCOPE = ROOT / ".kiro/current_task_files.txt"
STATE = ROOT / ".kiro/state/loop_state.json"
KEY = "AIza" + "B" * 35  # fake key-shaped string, built at runtime


def run(tool, inp):
    p = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(
        {"hook_event_name": "PreToolUse", "cwd": str(ROOT), "tool_name": tool, "tool_input": inp}),
        capture_output=True, text=True)
    if '"ask"' in p.stdout:
        got = "ask"
    elif p.returncode == 2:
        got = "block"
    elif p.returncode == 0:
        got = "allow"
    else:
        got = f"rc{p.returncode}"
    return got, (p.stderr or p.stdout).strip()[:110]


def main() -> int:
    saved_scope = SCOPE.read_text(encoding="utf-8") if SCOPE.exists() else None
    saved_state = STATE.read_text(encoding="utf-8") if STATE.exists() else None
    cases = []

    def case(name, expect, tool, inp):
        got, msg = run(tool, inp)
        cases.append((got == expect, name, expect, got, msg))

    try:
        SCOPE.unlink(missing_ok=True)
        STATE.unlink(missing_ok=True)
        case("no scope -> ask", "ask", "fs_write", {"path": "src/models.py", "text": "x"})
        SCOPE.write_text("src/models.py\ntests/test_lookup.py\nREADME.md\n", encoding="utf-8")
        case("in scope (relative)", "allow", "fs_write", {"path": "src/models.py", "text": "x"})
        case("in scope (absolute, odd case)", "allow", "str_replace",
             {"path": str(ROOT).upper() + r"\SRC\models.py", "oldStr": "a", "newStr": "b"})
        case("out of scope", "block", "fs_write", {"path": "src/cli.py", "text": "x"})
        case("RAPLH log always allowed", "allow", "fs_append", {"path": "docs/RAPLH_LOG.md", "text": "entry"})
        case("scope file itself -> ask", "ask", "fs_write", {"path": ".kiro/current_task_files.txt", "text": "x"})
        case("hook file -> ask", "ask", "fs_write", {"path": ".kiro/hooks/x.json", "text": "{}"})
        case("state dir blocked", "block", "fs_write", {"path": ".kiro/state/loop_state.json", "text": "{}"})
        case("write .env blocked", "block", "fs_write", {"path": ".env", "text": "GEMINI_API_KEY=x"})
        case("api key content blocked", "block", "fs_write", {"path": "src/models.py", "text": f"K='{KEY}'"})
        case("outside workspace blocked", "block", "fs_write", {"path": r"C:\Windows\Temp\x.py", "text": "x"})
        readme = (ROOT / "README.md").read_text(encoding="utf-8") if (ROOT / "README.md").exists() else \
            "# T\n## Working style\n<!-- candidate writes this without AI -->\n"
        case("README edit outside Working style ok", "allow", "fs_write",
             {"path": "README.md", "text": readme.replace("# ", "# (edited) ", 1)})
        case("README Working style rewrite blocked", "block", "fs_write",
             {"path": "README.md", "text": readme.split("## Working style")[0] + "## Working style\nI would take a break...\n"})
        case("shell read .env blocked", "block", "execute_pwsh", {"command": "Get-Content .env"})
        case("shell read .env.example ok", "allow", "execute_pwsh", {"command": "Get-Content .env.example"})
        case("shell print key var blocked", "block", "execute_pwsh", {"command": "echo $env:GEMINI_API_KEY"})
        case("git add .env blocked", "block", "execute_pwsh", {"command": "git add .env"})
        case("git push -> ask", "ask", "execute_pwsh", {"command": "git push -u origin main"})
        case("gh repo create -> ask", "ask", "execute_pwsh", {"command": "gh repo create rahn-assessment-x-y --public"})
        case("loop reset blocked for agent", "block", "execute_pwsh", {"command": "python .kiro/scripts/loop_guard.py --reset"})
        case("pytest allowed", "allow", "execute_pwsh", {"command": "python -m pytest -q"})
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"escalated": True}), encoding="utf-8")
        case("escalated -> writes locked", "block", "fs_write", {"path": "src/models.py", "text": "x"})
        case("escalated -> shell writes locked", "block", "execute_pwsh", {"command": "Set-Content src/models.py x"})
        case("escalated -> read-only shell ok", "allow", "execute_pwsh", {"command": "git status"})
    finally:
        for path, saved in ((SCOPE, saved_scope), (STATE, saved_state)):
            if saved is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(saved, encoding="utf-8")

    for ok, name, exp, got, msg in cases:
        detail = msg if (not ok or got != "allow") else ""
        print(f"{'PASS' if ok else 'FAIL'}  {name:36} expect={exp:5} got={got:5} {detail}")
    passed = sum(c[0] for c in cases)
    print(f"\n{passed}/{len(cases)} passed")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
