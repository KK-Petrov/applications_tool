"""Shared fixtures.

Safety first: every test runs with ``HUB_DATA_DIR`` pointing at a temporary folder, so no test can
ever read or write the owner's real data folder (CLAUDE.md rule 12). Every test also runs with
``HUB_CLAUDE_PATH`` pointing at the fake Claude Code (tests/fakes/fake_claude.py), so no test can reach the
real CLI; live tests (marker ``live``) remove it on purpose.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from helpers import BASE, FAKE_CLAUDE, FIXED_NOW, FormClient
from paths import FIXTURE_DATA, REPO_ROOT


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point HUB_DATA_DIR at an empty temporary folder unless a test asks for fixture data."""
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path_factory.mktemp("guard-data")))


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

    Claude Code is the fake, and its usage log goes to a temporary file (never the repository's reports/).
    """
    from hub.config import load_config

    cfg = load_config()
    assert cfg.storage.data_dir == data_dir
    claude = cfg.claude.model_copy(
        update={"path": str(FAKE_CLAUDE), "usage_log": str(tmp_path / "ai_usage.jsonl")}
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
