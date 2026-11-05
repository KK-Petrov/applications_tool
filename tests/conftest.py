"""Shared fixtures.

Safety first: every test runs with ``HUB_DATA_DIR`` pointing at a temporary folder, so no test can
ever read or write the owner's real data folder (CLAUDE.md rule 12).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from paths import FIXTURE_DATA, REPO_ROOT


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point HUB_DATA_DIR at an empty temporary folder unless a test asks for fixture data."""
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path_factory.mktemp("guard-data")))


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temporary data_dir filled from tests/fixtures/data/ and exported as HUB_DATA_DIR."""
    target = tmp_path / "data"
    shutil.copytree(FIXTURE_DATA, target)
    monkeypatch.setenv("HUB_DATA_DIR", str(target))
    return target


@pytest.fixture
def config(data_dir: Path):
    """The project config with data_dir pointing at the temporary fixture copy."""
    from hub.config import load_config

    cfg = load_config()
    assert cfg.storage.data_dir == data_dir
    return cfg


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Everything under tests/e2e/ is an e2e test, even if a file forgets the marker."""
    e2e_dir = REPO_ROOT / "tests" / "e2e"
    for item in items:
        if e2e_dir in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.e2e)
