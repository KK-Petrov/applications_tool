#!/usr/bin/env python3
"""PostToolUse hook: format and lint the file Claude just edited.

Commands per file extension come from .claude/hooks/hooks.json ("lint"). A tool that isn't installed
yet is skipped, so the hook is harmless before Phase 0 sets up the environment.
Exit 2 feeds remaining problems back to Claude; exit 0 means all good or nothing to do.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])


def resolve(exe: str, cwd: Path) -> str | None:
    """Find a tool in the project's virtualenv or node_modules first, then on PATH."""
    names = [exe, exe + ".exe", exe + ".cmd"]
    for base in (cwd, ROOT):
        for sub in (".venv/bin", ".venv/Scripts", "node_modules/.bin"):
            for name in names:
                candidate = base / sub / name
                if candidate.is_file():
                    return str(candidate)
    return shutil.which(exe)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    file_path = (payload.get("tool_input") or {}).get("file_path") or ""
    if not file_path or not Path(file_path).is_file():
        return 0
    cfg_path = ROOT / ".claude" / "hooks" / "hooks.json"
    if not cfg_path.exists():
        return 0
    lint = json.loads(cfg_path.read_text(encoding="utf-8")).get("lint", {})
    steps = lint.get(Path(file_path).suffix.lower(), [])
    problems = []
    for step in steps:
        cwd = ROOT / step.get("cwd", ".")
        if not cwd.is_dir():
            continue
        exe = resolve(step["cmd"][0], cwd)
        if exe is None:
            continue
        args = [exe] + [a.replace("{file}", str(Path(file_path).resolve())) for a in step["cmd"][1:]]
        try:
            res = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            problems.append(f"{step['cmd'][0]} could not run: {exc}")
            continue
        if res.returncode != 0:
            out = (res.stdout + res.stderr).strip().splitlines()
            problems.append(f"{step['cmd'][0]} reported issues:\n" + "\n".join(out[-40:]))
    if problems:
        print(f"Lint/format issues in {file_path} that need fixing:\n" + "\n\n".join(problems), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
