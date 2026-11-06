"""A stand-in for the Claude Code CLI (``claude -p``) used by every non-live test (docs/04 §1.1).

The wrapper runs it in place of the real binary when ``claude.path`` (or ``HUB_CLAUDE_PATH``) points here.
It never contacts anything. Behaviour is chosen by environment variables:

- ``FAKE_CLAUDE_MODE``: ``ok`` (default) prints the recorded CLI result for the skill named at the start of
  the ``-p`` prompt (``tests/fakes/recordings/<skill>.json``, or ``FAKE_CLAUDE_RECORDING``);
  ``timeout`` starts a child process (its pid written to ``FAKE_CLAUDE_PIDFILE``) and both sleep, so the
  wrapper must kill the whole process tree; ``exit`` writes two stderr lines and exits 1; ``invalid_json``
  prints text that is not JSON; ``is_error`` prints a result with ``is_error: true``; ``no_structured``
  prints a successful result without ``structured_output``; ``schema_mismatch`` prints a
  ``structured_output`` that breaks the rec-email schema.
- ``FAKE_CLAUDE_LOG``: if set, one JSON line per call is appended: argv, stdin and cwd (for assertions).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

RECORDINGS = Path(__file__).resolve().parent / "recordings"
SLEEP_S = 60  # timeout mode; FAKE_CLAUDE_SLEEP shortens it (e.g. to hold a busy state in a browser test)


def _skill(argv: list[str]) -> str:
    """The skill whose SKILL.md is passed, e.g. ``…/rec-email/SKILL.md`` → rec-email."""
    if "--append-system-prompt-file" not in argv:
        return ""
    return Path(argv[argv.index("--append-system-prompt-file") + 1]).parent.name


def _result(**extra: object) -> str:
    base: dict[str, object] = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "duration_ms": 1234,
        "num_turns": 1,
        "result": "",
        "session_id": "00000000-0000-4000-8000-000000000000",
        "total_cost_usd": 0.0123,
        "permission_denials": [],
    }
    base.update(extra)
    return json.dumps(base)


def main() -> int:
    # Like the real CLI, read and write UTF-8 whatever the console code page (Windows: cp1252).
    sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    argv = sys.argv[1:]
    stdin = sys.stdin.read()
    log = os.environ.get("FAKE_CLAUDE_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"argv": argv, "stdin": stdin, "cwd": os.getcwd()}) + "\n")
    mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
    if mode == "ok":
        path = Path(os.environ.get("FAKE_CLAUDE_RECORDING") or RECORDINGS / f"{_skill(argv)}.json")
        if not path.is_file():
            print(f"fake_claude: no recording {path.name}", file=sys.stderr)
            return 3
        sys.stdout.write(path.read_text(encoding="utf-8"))
        return 0
    if mode == "timeout":
        sleep_s = float(os.environ.get("FAKE_CLAUDE_SLEEP", SLEEP_S))
        child = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({sleep_s})"])
        pidfile = os.environ.get("FAKE_CLAUDE_PIDFILE")
        if pidfile:
            Path(pidfile).write_text(f"{os.getpid()}\n{child.pid}\n", encoding="utf-8")
        time.sleep(sleep_s)
        return 0
    if mode == "exit":
        print("fake failure: boom\nsecond line with details", file=sys.stderr)
        return 1
    if mode == "invalid_json":
        print("this is not JSON {")
        return 0
    if mode == "is_error":
        print(_result(subtype="error_during_execution", is_error=True, result="Something went wrong"))
        return 0
    if mode == "no_structured":
        print(_result(result="Here is your email"))
        return 0
    if mode == "schema_mismatch":
        print(_result(structured_output={"subject": 5, "body": "x"}))
        return 0
    print(f"fake_claude: unknown mode {mode}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
