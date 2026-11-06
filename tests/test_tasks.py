"""tasks.py runs the suites in parallel (DECISIONS #38) and warns when test-fast nears its C-0-02 limit."""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Iterator
from types import ModuleType

import pytest

from hub.config import load_config

from paths import REPO_ROOT


@pytest.fixture
def tasks() -> Iterator[ModuleType]:
    spec = importlib.util.spec_from_file_location("tasks_under_test", REPO_ROOT / "tasks.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module


@pytest.fixture
def calls(tasks: ModuleType, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Record pytest invocations instead of running them; each returns 0 (passed)."""
    seen: list[tuple[str, ...]] = []
    monkeypatch.setattr(tasks, "_pytest", lambda *args: seen.append(args) or 0)
    return seen


def _clock(monkeypatch: pytest.MonkeyPatch, tasks: ModuleType, elapsed: float) -> None:
    ticks = iter([100.0, 100.0 + elapsed])
    monkeypatch.setattr(tasks.time, "perf_counter", lambda: next(ticks))


def test_config_holds_the_worker_count_and_warning() -> None:
    perf = load_config().performance
    assert perf.test_workers >= 1
    limit = json.loads((REPO_ROOT / "config" / "criteria.json").read_text(encoding="utf-8"))
    c002 = next(c for c in limit["criteria"] if c["id"] == "C-0-02")
    assert 0 < perf.test_fast_warn_seconds < c002["threshold"]


def test_test_fast_runs_in_parallel_with_the_configured_workers(tasks, calls, monkeypatch) -> None:
    _clock(monkeypatch, tasks, 10.0)
    assert tasks.test_fast([]) == 0
    workers = str(load_config().performance.test_workers)
    parallel = calls[0]
    assert parallel[parallel.index("-n") + 1] == workers
    assert "not e2e and not live and not serial" in parallel


def test_extra_args_come_last_so_n0_forces_a_serial_run(tasks, calls, monkeypatch) -> None:
    _clock(monkeypatch, tasks, 10.0)
    tasks.test_fast(["-n", "0"])
    assert calls[0][-2:] == ("-n", "0")


def test_test_runs_serial_tests_in_a_second_non_parallel_pass(tasks, calls) -> None:
    assert tasks.test([]) == 0
    assert len(calls) == 2
    assert "-n" in calls[0] and "not live and not serial" in calls[0]
    assert "-n" not in calls[1] and "serial and not live" in calls[1]


def test_no_serial_tests_selected_is_not_a_failure(tasks, monkeypatch) -> None:
    codes = iter([0, 5])  # pytest exit code 5 = no tests collected
    monkeypatch.setattr(tasks, "_pytest", lambda *args: next(codes))
    assert tasks.test([]) == 0


def test_a_failure_in_either_pass_fails_the_target(tasks, monkeypatch) -> None:
    codes = iter([1, 0])
    monkeypatch.setattr(tasks, "_pytest", lambda *args: next(codes))
    assert tasks.test([]) == 1


def test_warning_above_the_configured_seconds(tasks, calls, monkeypatch, capsys) -> None:
    warn = load_config().performance.test_fast_warn_seconds
    _clock(monkeypatch, tasks, warn + 0.5)
    tasks.test_fast([])
    out = capsys.readouterr().out
    assert "WARNING: test-fast took" in out and "C-0-02" in out


def test_no_warning_below_it(tasks, calls, monkeypatch, capsys) -> None:
    warn = load_config().performance.test_fast_warn_seconds
    _clock(monkeypatch, tasks, warn - 0.5)
    tasks.test_fast([])
    assert "WARNING" not in capsys.readouterr().out
