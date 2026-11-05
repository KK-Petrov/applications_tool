#!/usr/bin/env python3
"""Stop hook: when Claude finishes a turn, run the criteria guard, then the fast tests if code changed.

On failure, exit 2 once so Claude sees the problem; the stop_hook_active flag prevents loops.
Skip the fast tests (not the guard) by creating .claude/.skip-stop-tests.
Configuration: .claude/hooks/hooks.json ("test_fast"). Standard library only.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
sys.path.insert(0, str(Path(__file__).resolve().parent))
import criteria_guard  # noqa: E402


def changed_paths(watch: list[str]) -> bool:
    if shutil.which("git") is None:
        return False
    inside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=ROOT, capture_output=True, text=True)
    if inside.returncode != 0:
        return False
    res = subprocess.run(["git", "status", "--porcelain", "--", *watch], cwd=ROOT, capture_output=True, text=True)
    return bool(res.stdout.strip())


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}
    if payload.get("stop_hook_active"):
        return 0

    ok, msg = criteria_guard.check()
    if not ok:
        print(msg, file=sys.stderr)
        return 2

    if (ROOT / ".claude" / ".skip-stop-tests").exists():
        return 0
    cfg_path = ROOT / ".claude" / "hooks" / "hooks.json"
    if not cfg_path.exists():
        return 0
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")).get("test_fast", {})
    cmd = cfg.get("command")
    if not cmd or shutil.which(cmd[0]) is None:
        return 0
    needs = cfg.get("requires_file")
    if needs and not (ROOT / needs).exists():
        return 0
    if not changed_paths(cfg.get("watch", ["src", "tests"])):
        return 0
    try:
        res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=cfg.get("timeout", 120))
    except subprocess.TimeoutExpired:
        print(f"Fast tests took longer than {cfg.get('timeout', 120)} s. Keep the fast suite fast, or mark slow tests.", file=sys.stderr)
        return 2
    out = res.stdout + res.stderr
    if res.returncode != 0:
        not_yet = ("No rule to make target", "Missing script:", "Recipe `", "No such task", "could not find task")
        if any(marker in out for marker in not_yet):
            return 0  # the fast-test target doesn't exist yet (before Phase 0 creates it)
        print("Fast tests failed after your changes. Fix them, or explain why they fail, before finishing:", file=sys.stderr)
        print("\n".join(out.strip().splitlines()[-60:]), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
