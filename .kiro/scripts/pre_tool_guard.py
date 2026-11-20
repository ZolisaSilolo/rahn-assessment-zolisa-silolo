#!/usr/bin/env python3
"""PreToolUse guard for the RAHN assessment workspace.

Enforces the RAHN playbook loop guardrails (section 04) and assessment rules:
  * the agent only changes files listed for the current task (.kiro/current_task_files.txt)
  * .env is never read or written by the agent; API-key-like content is never written
  * the README "Working style" section is never written by AI (assessment rule 3)
  * git add/commit/push can never include .env
  * external actions (push, gh repo ..., web calls) need human approval
  * once loop_guard.py escalates, file changes are locked until the human resets it

Kiro passes {"tool_name": ..., "tool_input": {...}, "cwd": ...} on stdin.
Exit 0 = allow, exit 2 = block (stderr goes to the agent), stdout JSON "ask" = prompt the human.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOOP_STATE = ROOT / ".kiro" / "state" / "loop_state.json"
SCOPE_FILE = ROOT / ".kiro" / "current_task_files.txt"

WRITE_TOOLS = {"fs_write", "fs_append", "str_replace", "delete_file"}
ALWAYS_ALLOWED = ["docs/raplh_log.md", ".kiro/specs/*/tasks.md"]
ASK_PATHS = [".kiro/hooks/*", ".kiro/scripts/*", ".kiro/specs/*", ".kiro/steering/*",
             ".kiro/current_task_files.txt"]
SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),                 # Google / Gemini API key
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
ENV_TOKEN = re.compile(r"(?<![\w.\-])\.env(?![\w.\-])")    # .env but not .env.example / .venv
WS_HEADING = re.compile(r"^##\s+Working style\s*$", re.IGNORECASE | re.MULTILINE)
SHELL_WRITE = re.compile(r"(set-content|add-content|out-file|new-item|remove-item|move-item|"
                         r"copy-item|rename-item|\bdel\b|\brm\b|\bmv\b|\bcp\b|>)")


def deny(msg: str) -> None:
    print(f"[pre-tool-guard] BLOCKED: {msg}", file=sys.stderr)
    sys.exit(2)


def ask(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"permissionDecision": "ask",
                                             "permissionDecisionReason": reason}}))
    sys.exit(0)


def allow() -> None:
    sys.exit(0)


def matches(rel_lower: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel_lower, p.lower()) for p in patterns)


def rel_path(raw: str, cwd: str) -> str | None:
    p = Path(raw)
    if not p.is_absolute():
        p = Path(cwd or ROOT) / p
    full = os.path.abspath(p)
    root = os.path.abspath(ROOT)
    if os.path.normcase(full) != os.path.normcase(root) and not os.path.normcase(full).startswith(
            os.path.normcase(root) + os.sep):
        return None
    return Path(os.path.relpath(full, root)).as_posix()


def loop_escalated() -> bool:
    try:
        return bool(json.loads(LOOP_STATE.read_text(encoding="utf-8")).get("escalated"))
    except (OSError, ValueError):
        return False


def load_scope() -> list[str]:
    try:
        lines = SCOPE_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [ln.strip().replace("\\", "/") for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def new_content(tool: str, inp: dict, path: Path) -> str:
    old = read_text(path)
    if tool == "fs_write":
        return str(inp.get("text", ""))
    if tool == "fs_append":
        return old + "\n" + str(inp.get("text", ""))
    if tool == "str_replace":
        old_s, new_s = str(inp.get("oldStr", "")), str(inp.get("newStr", ""))
        return old.replace(old_s, new_s) if inp.get("replace_all") else old.replace(old_s, new_s, 1)
    return ""  # delete_file


def working_style_body(text: str) -> str:
    m = WS_HEADING.search(text or "")
    if not m:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"^#{1,2}\s", rest, re.MULTILINE)   # next top-level or level-2 heading
    body = rest[: nxt.start()] if nxt else rest
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    return re.sub(r"\s+", " ", body).strip()


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=20)


def env_is_tracked() -> bool:
    return git("ls-files", "--cached", "--error-unmatch", ".env").returncode == 0


def env_is_ignored() -> bool:
    return git("check-ignore", "-q", ".env").returncode == 0


def handle_write(tool: str, inp: dict, cwd: str) -> None:
    raw = inp.get("path") or inp.get("targetFile") or inp.get("file_path")
    if not raw:
        deny(f"could not find the target path in {tool} input; refusing to guess.")
    rel = rel_path(str(raw), cwd)
    if rel is None:
        deny(f"{raw} is outside the workspace.")
    low = rel.lower()

    if loop_escalated():
        deny("the RAPLH loop is ESCALATED (same failure repeated). Stop and hand over to the human "
             "with the failing test output. Only the human can run "
             "`python .kiro/scripts/loop_guard.py --reset`.")
    if low.startswith(".kiro/state/"):
        deny(".kiro/state is written only by loop_guard.py.")
    if low == ".env" or low.endswith("/.env"):
        deny(".env holds the real API key. The human creates and edits it; the agent never does.")

    blob = "\n".join(str(v) for v in inp.values() if isinstance(v, str))
    if any(p.search(blob) for p in SECRET_PATTERNS):
        deny("the content looks like an API key or private key. Keys live only in .env.")

    if low == "readme.md":
        path = ROOT / rel
        if working_style_body(read_text(path)) != working_style_body(new_content(tool, inp, path)):
            deny("the README 'Working style' section must be written by the candidate without AI "
                 "(assessment rule 3). You may only add the heading with an HTML-comment placeholder, "
                 "and must leave the candidate's text unchanged.")

    if matches(low, ALWAYS_ALLOWED):
        allow()
    if matches(low, ASK_PATHS):
        ask(f"The agent wants to change {rel}, a Kiro control file (scope, hooks, scripts or spec). Approve?")
    scope = load_scope()
    if not scope:
        ask(f"No task scope is set (.kiro/current_task_files.txt is missing or empty). Allow a change to {rel}?")
    if matches(low, scope):
        allow()
    deny(f"{rel} is not in the current task scope ({', '.join(scope)}). Stop and ask the human to "
         f"extend .kiro/current_task_files.txt if this file is really needed.")


def handle_shell(inp: dict) -> None:
    cmd = str(inp.get("command", ""))
    c = cmd.lower()

    if re.search(r"loop_guard\.py\b.*--reset", c):
        deny("only the human may reset the loop guard.")
    if loop_escalated() and SHELL_WRITE.search(c):
        deny("the RAPLH loop is ESCALATED; file-changing commands are locked until the human resets it.")

    if ENV_TOKEN.search(c) and re.search(
            r"\b(get-content|gc|cat|type|more|less|select-string|sls|findstr|copy-item|cp|notepad|code|"
            r"import-csv|get-item|python|py)\b", c):
        deny("reading .env would expose the API key in the agent's context.")
    if re.search(r"\$env:gemini_api_key|\$\{?gemini_api_key|%gemini_api_key%|\bprintenv\b|"
                 r"\b(get-childitem|gci|dir|ls)\s+env:", c):
        deny("printing environment variables could expose the API key.")

    if re.search(r"\bgit\b[^;|&]*\badd\b", c):
        if ENV_TOKEN.search(c):
            deny("never stage .env.")
        if re.search(r"\badd\b[^;|&]*(\s-a\b|\s--all\b|\s\.(?=\s|$|;)|\s-u\b)", c) and (ROOT / ".env").exists() \
                and not env_is_ignored():
            deny(".env exists and is not git-ignored. Add it to .gitignore before a broad `git add`.")

    if re.search(r"\bgit\b[^;|&]*\b(commit|push)\b", c):
        if env_is_tracked():
            deny(".env is tracked by git. Run `git rm --cached .env` (human) before committing or pushing.")
        staged = git("diff", "--cached").stdout
        if any(p.search(staged) for p in SECRET_PATTERNS):
            deny("the staged changes contain an API-key-like string.")

    if re.search(r"\bgit\b[^;|&]*\bpush\b", c) or re.search(
            r"\bgh\s+(repo\s+(create|edit|delete|rename|archive)|pr\s+(create|merge)|release\s+create|api)\b", c) \
            or re.search(r"\b(invoke-webrequest|invoke-restmethod|iwr|irm|curl|wget|send-mailmessage)\b", c):
        ask(f"External action (RAPLH H-step needs human approval): {cmd[:200]}")
    allow()


def main() -> None:
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError as exc:
        deny(f"unreadable hook payload ({exc}).")
    tool = str(payload.get("tool_name", "")).split("::")[-1]
    inp = payload.get("tool_input") or {}
    if isinstance(inp, str):
        try:
            inp = json.loads(inp)
        except ValueError:
            inp = {"command": inp}
    cwd = str(payload.get("cwd") or ROOT)
    if tool in WRITE_TOOLS:
        handle_write(tool, inp, cwd)
    elif tool.startswith("execute_"):
        handle_shell(inp)
    allow()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # fail closed, but say why
        print(f"[pre-tool-guard] internal error, blocking to be safe: {exc!r}", file=sys.stderr)
        sys.exit(2)
