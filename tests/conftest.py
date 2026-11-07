"""Shared fixtures.

Safety first: every test runs with ``HUB_DATA_DIR`` pointing at a temporary folder, so no test can
ever read or write the owner's real data folder (CLAUDE.md rule 12). Every test also runs with
``HUB_CLAUDE_PATH`` pointing at the fake Claude Code (tests/fakes/fake_claude.py), so no test can reach the
real CLI; live tests (marker ``live``) remove it on purpose. And no non-live test may use an AI usage log
outside its own ``tmp_path`` (``_usage_logs_stay_in_tmp``, DECISIONS #60).
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from helpers import BASE, FAKE_CLAUDE, FIXED_NOW, FormClient, UsageLogOutsideTmp, check_usage_log
from paths import FIXTURE_DATA, REPO_ROOT


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point HUB_DATA_DIR at an empty folder in the test's tmp_path unless a test asks for fixture data."""
    guard = tmp_path / "guard-data"
    guard.mkdir()
    monkeypatch.setenv("HUB_DATA_DIR", str(guard))


@pytest.fixture(autouse=True)
def _usage_logs_stay_in_tmp(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[list[str]]:
    """Fail any non-live test that resolves the app or run usage log outside its tmp_path.

    Every write and read of a usage log goes through ``hub.ai.usage.app_log_path`` / ``run_log_path``; both
    are wrapped here, so a config that was not redirected (the test's own, or the e2e server's) fails the
    test at once and again at teardown (in case the error was swallowed, e.g. inside the server thread).
    """
    violations: list[str] = []
    if request.node.get_closest_marker("live"):
        yield violations
        return
    from hub.ai import usage

    def guarded(real):  # noqa: ANN001, ANN202
        def resolve(config):  # noqa: ANN001, ANN202
            try:
                return check_usage_log(real(config), tmp_path)
            except UsageLogOutsideTmp as err:
                violations.append(str(err))
                raise

        return resolve

    monkeypatch.setattr(usage, "app_log_path", guarded(usage.app_log_path))
    monkeypatch.setattr(usage, "run_log_path", guarded(usage.run_log_path))
    yield violations
    if violations:
        pytest.fail("\n".join(dict.fromkeys(violations)), pytrace=False)


@pytest.fixture(autouse=True)
def _fake_claude(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every non-live test talks to the fake Claude Code in its default ("ok") mode."""
    if request.node.get_closest_marker("live"):
        monkeypatch.delenv("HUB_CLAUDE_PATH", raising=False)
        return
    monkeypatch.setenv("HUB_CLAUDE_PATH", str(FAKE_CLAUDE))
    for name in (
        "FAKE_CLAUDE_MODE",
        "FAKE_CLAUDE_LOG",
        "FAKE_CLAUDE_PIDFILE",
        "FAKE_CLAUDE_RECORDING",
        "FAKE_CLAUDE_SLEEP",
        "FAKE_CLAUDE_DELAY",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temporary data_dir filled from tests/fixtures/data/ and exported as HUB_DATA_DIR."""
    target = tmp_path / "data"
    shutil.copytree(FIXTURE_DATA, target)
    monkeypatch.setenv("HUB_DATA_DIR", str(target))
    return target


@pytest.fixture
def config(data_dir: Path, tmp_path: Path):
    """The project config with data_dir pointing at the temporary fixture copy.

    Claude Code is the fake. The app's usage log is ``<data_dir>/logs/ai_usage.jsonl`` (inside the temporary
    copy); the eval/live run log goes to a temporary file, never the repository's reports/.
    """
    from hub.config import load_config

    cfg = load_config()
    assert cfg.storage.data_dir == data_dir
    claude = cfg.claude.model_copy(
        update={"path": str(FAKE_CLAUDE), "run_usage_log": str(tmp_path / "run_usage.jsonl")}
    )
    return cfg.model_copy(update={"claude": claude})


@pytest.fixture
def client(config):
    """The app on its own origin with the clock fixed at 30 Oct 2026 12:00 New York.

    Like a browser, it sends the app's own ``Origin`` on every request (DECISIONS #26). Redirects are
    not followed so tests can assert the 303 of the no-JS fallback.
    """
    from hub.app import create_app

    return FormClient(
        create_app(config, clock=lambda: FIXED_NOW),
        base_url=BASE,
        headers={"Origin": BASE},
        follow_redirects=False,
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Everything under tests/e2e/ is an e2e test, even if a file forgets the marker."""
    e2e_dir = REPO_ROOT / "tests" / "e2e"
    for item in items:
        if e2e_dir in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.e2e)
