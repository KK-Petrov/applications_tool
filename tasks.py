"""Single entry point for project tasks: ``uv run python tasks.py <target> [extra args]``.

Standard library only and cross-platform: every command is a list run with the current interpreter,
with no shell syntax. Keep this list in step with CLAUDE.md → Commands.
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable


def _run(*cmd: str) -> int:
    print("$ " + " ".join(cmd), flush=True)
    return subprocess.run(list(cmd), cwd=ROOT, check=False).returncode


def _pytest(*args: str) -> int:
    return _run(PY, "-m", "pytest", *args)


def dev(extra: list[str]) -> int:
    """Run the app with auto-reload at the configured address (http://127.0.0.1:8765)."""
    return _run(PY, "-m", "hub.cli", "serve", "--reload", *extra)


def init_data(extra: list[str]) -> int:
    """Prepare the data folder (copies the seed programmes, asks for owner name and email)."""
    return _run(PY, "-m", "hub.cli", "init-data", *extra)


def test_fast(extra: list[str]) -> int:
    """Fast tests: everything except e2e and live; must finish in under 60 s (C-0-02)."""
    start = time.perf_counter()
    code = _pytest("-m", "not e2e and not live", "-q", *extra)
    print(f"test-fast finished in {time.perf_counter() - start:.1f} s", flush=True)
    return code


def test(extra: list[str]) -> int:
    """All tests except live ones (real Google or Claude calls)."""
    return _pytest("-m", "not live", *extra)


def e2e(extra: list[str]) -> int:
    """End-to-end browser tests only."""
    return _pytest("-m", "e2e", *extra)


def live(extra: list[str]) -> int:
    """Live tests against real Google or Claude; pass -k to pick one, e.g. `live -k calendar`."""
    return _pytest("-m", "live", *extra)


def eval_email(extra: list[str]) -> int:
    """Email generator eval set (Phase 2b)."""
    print("eval-email: not yet implemented (Phase 2b).")
    return 0


def eval_essay(extra: list[str]) -> int:
    """Essay evaluator eval set (Phase 4b)."""
    print("eval-essay: not yet implemented (Phase 4b).")
    return 0


def lint(extra: list[str]) -> int:
    """Ruff lint and format check."""
    return _run(PY, "-m", "ruff", "check", ".", *extra) or _run(PY, "-m", "ruff", "format", "--check", ".")


def validate(extra: list[str]) -> int:
    """Lint, all non-live tests, then the criteria guard."""
    for step in (lambda: lint([]), lambda: test([]), lambda: _run(PY, ".claude/hooks/criteria_guard.py")):
        code = step()
        if code:
            return code
    return 0


TARGETS: dict[str, Callable[[list[str]], int]] = {
    "dev": dev,
    "init-data": init_data,
    "test-fast": test_fast,
    "test": test,
    "e2e": e2e,
    "live": live,
    "eval-email": eval_email,
    "eval-essay": eval_essay,
    "lint": lint,
    "validate": validate,
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in TARGETS:
        print("usage: uv run python tasks.py <target> [args]\n\ntargets:")
        for name, func in TARGETS.items():
            print(f"  {name:<11} {(func.__doc__ or '').strip()}")
        return 2 if argv else 0
    return TARGETS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
