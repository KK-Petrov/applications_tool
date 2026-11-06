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


NO_TESTS_COLLECTED = 5  # pytest exit code when a marker expression selects nothing


def _performance() -> tuple[int, float]:
    """Worker count and test-fast warning from config/project.yaml → performance (rule 5).

    Imported lazily so the other targets stay standard library only.
    """
    from hub.config import load_config

    perf = load_config().performance
    return perf.test_workers, perf.test_fast_warn_seconds


def _parallel_then_serial(markers: str, extra: list[str]) -> int:
    """Run ``markers`` minus ``serial`` tests across pytest-xdist workers, then the ``serial`` ones alone.

    Timing-sensitive tests (marker ``serial``, e.g. C-2a-03) run in their own non-parallel pass so other
    workers cannot slow them down. ``extra`` comes last, so ``-n 0`` forces the first pass to run serially.
    """
    workers, _ = _performance()
    first = _pytest("-n", str(workers), "-m", f"{markers} and not serial", *extra)
    second = _pytest("-m", f"serial and {markers}", *extra)
    if second == NO_TESTS_COLLECTED:
        second = 0
    return first or second


def test_fast(extra: list[str]) -> int:
    """Fast tests in parallel: everything except e2e and live; must finish in under 60 s (C-0-02)."""
    _, warn_seconds = _performance()
    start = time.perf_counter()
    code = _parallel_then_serial("not e2e and not live", ["-q", *extra])
    elapsed = time.perf_counter() - start
    print(f"test-fast finished in {elapsed:.1f} s", flush=True)
    if elapsed > warn_seconds:
        print(
            f"WARNING: test-fast took {elapsed:.1f} s (warning at {warn_seconds:g} s, limit 60 s, C-0-02)",
            flush=True,
        )
    return code


def test(extra: list[str]) -> int:
    """All tests except live ones (real Google or Claude calls), in parallel; serial-marked tests after."""
    return _parallel_then_serial("not live", extra)


def e2e(extra: list[str]) -> int:
    """End-to-end browser tests only."""
    return _pytest("-m", "e2e", *extra)


def live(extra: list[str]) -> int:
    """Live tests against real Google or Claude; pass -k to pick one, e.g. `live -k calendar`."""
    return _pytest("-m", "live", *extra)


def eval_email(extra: list[str]) -> int:
    """Email eval (live Claude calls): reports/eval_email.json and .md; options --cases 1,2 and --out DIR."""
    return _run(PY, "tests/evals/email/run.py", *extra)


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
