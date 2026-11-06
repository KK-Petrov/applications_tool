"""Cross-site request and DNS-rebinding protection for the local app (DECISIONS #26).

- Host must be 127.0.0.1:<port> or localhost:<port> (the port the server is bound to), else 400.
- Writes (POST/PUT/PATCH/DELETE): an Origin header must be the app's own origin, else 403; without an
  Origin, a Referer from the app's own origin is required, else 403. A rejected request writes nothing.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hub.app import create_app
from hub.config import load_config

from helpers import BASE, FIXED_NOW, tree_digest
from paths import FIXTURE_DATA

VERIFY = "/programmes/princeton-mfin/verify"


@pytest.fixture(scope="module")
def shared(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[TestClient, Path]]:
    """One app for the tests whose requests must be refused or only read (built once per module and worker).

    It has its own copy of the fixture data. Teardown proves that none of those requests wrote anything.
    Tests that write on purpose use ``bare`` (a fresh app and data_dir each).
    """
    folder = tmp_path_factory.mktemp("security-shared") / "data"
    shutil.copytree(FIXTURE_DATA, folder)
    cfg = load_config(env={"HUB_DATA_DIR": str(folder)})
    assert cfg.storage.data_dir == folder
    before = tree_digest(folder)
    app = create_app(cfg, clock=lambda: FIXED_NOW)
    yield TestClient(app, base_url=BASE, follow_redirects=False), folder
    assert tree_digest(folder) == before, "a refused or read-only request changed the shared data folder"


@pytest.fixture
def bare(config) -> TestClient:
    """A fresh client on the app's own host that sends no Origin or Referer unless a test adds one."""
    return TestClient(create_app(config, clock=lambda: FIXED_NOW), base_url=BASE, follow_redirects=False)


def _princeton(data_dir: Path) -> bytes:
    return (data_dir / "programmes" / "princeton-mfin.yaml").read_bytes()


@pytest.mark.parametrize(
    "origin", ["https://evil.example", "null", "http://127.0.0.1:9999", "http://127.0.0.1"]
)
def test_foreign_origin_is_refused_and_nothing_written(shared: tuple[TestClient, Path], origin: str) -> None:
    bare, data_dir = shared
    before = tree_digest(data_dir)
    res = bare.post(VERIFY, headers={"Origin": origin})
    assert res.status_code == 403
    assert tree_digest(data_dir) == before


def test_no_origin_and_no_referer_is_refused(shared: tuple[TestClient, Path]) -> None:
    bare, data_dir = shared
    before = _princeton(data_dir)
    assert bare.post(VERIFY).status_code == 403
    assert _princeton(data_dir) == before


@pytest.mark.parametrize(
    "referer", ["https://evil.example/programmes/princeton-mfin", "http://127.0.0.1:8765.evil.example/"]
)
def test_foreign_referer_is_refused(shared: tuple[TestClient, Path], referer: str) -> None:
    bare, data_dir = shared
    before = _princeton(data_dir)
    assert bare.post(VERIFY, headers={"Referer": referer}).status_code == 403
    assert _princeton(data_dir) == before


def test_foreign_origin_wins_over_own_referer(shared: tuple[TestClient, Path]) -> None:
    bare, data_dir = shared
    before = _princeton(data_dir)
    res = bare.post(VERIFY, headers={"Origin": "https://evil.example", "Referer": BASE + "/"})
    assert res.status_code == 403
    assert _princeton(data_dir) == before


@pytest.mark.parametrize(
    "host", ["evil.example", "127.0.0.1.evil.example:8765", "127.0.0.1:9999", "192.168.1.5:8765"]
)
def test_wrong_host_is_rejected(shared: tuple[TestClient, Path], host: str) -> None:
    bare, data_dir = shared
    assert bare.get("/", headers={"Host": host}).status_code == 400


def test_wrong_host_write_is_rejected_and_nothing_written(shared: tuple[TestClient, Path]) -> None:
    bare, data_dir = shared
    before = tree_digest(data_dir)
    res = bare.post(VERIFY, headers={"Host": "evil.example", "Origin": "http://evil.example"})
    assert res.status_code == 400
    assert tree_digest(data_dir) == before


def test_rejection_page_shows_no_data(shared: tuple[TestClient, Path]) -> None:
    bare, data_dir = shared
    res = bare.get("/", headers={"Host": "evil.example"})
    assert "Princeton" not in res.text and "Application Hub" not in res.text


def test_same_origin_write_succeeds(bare: TestClient, data_dir: Path) -> None:
    res = bare.post(VERIFY, headers={"Origin": BASE})
    assert res.status_code == 303
    assert "verification: verified" in _princeton(data_dir).decode("utf-8")


def test_same_origin_referer_without_origin_succeeds(bare: TestClient, data_dir: Path) -> None:
    res = bare.post(VERIFY, headers={"Referer": BASE + "/programmes/princeton-mfin"})
    assert res.status_code == 303
    assert "verification: verified" in _princeton(data_dir).decode("utf-8")


def test_localhost_is_accepted(config, data_dir: Path) -> None:
    local = "http://localhost:8765"
    client = TestClient(create_app(config, clock=lambda: FIXED_NOW), base_url=local, follow_redirects=False)
    assert client.get("/").status_code == 200
    assert client.post(VERIFY, headers={"Origin": local}).status_code == 303


def test_htmx_request_works(bare: TestClient, data_dir: Path) -> None:
    res = bare.post(
        "/programmes/princeton-mfin/requirements/cv",
        data={"done": "1"},
        headers={"Origin": BASE, "HX-Request": "true"},
    )
    assert res.status_code == 200
    assert "<html" not in res.text  # a partial, not a page


def test_gets_need_no_origin(shared: tuple[TestClient, Path]) -> None:
    bare, data_dir = shared
    for route in ("/", "/programmes", "/programmes/princeton-mfin", "/static/css/tokens.css"):
        assert bare.get(route).status_code == 200, route


# ---- Phase 2a write routes: a foreign Origin is refused and nothing is written -----------------------

NEW_WRITE_ROUTES = {
    "status cell": ("/recommenders/jansen/letters/princeton-mfin", {"data": {"status": "requested"}}),
    "add professor": ("/recommenders", {"data": {"name": "Prof. X Evil", "email": "x@example.com"}}),
    "edit professor": ("/recommenders/jansen", {"data": {"name": "Prof. X Evil", "email": "x@example.com"}}),
    "csv preview": (
        "/recommenders/import",
        {
            "files": {
                "file": ("p.csv", b"name,email,university,course\r\nProf. X Evil,x@example.com,RSM,C\r\n")
            }
        },
    ),
    "csv save": (
        "/recommenders/import/save",
        {"data": {"csv_text": "name,email\r\nProf. X Evil,x@example.com\r\n"}},
    ),
    "mark as requested": ("/recommenders/jansen/mark-requested", {"data": {"programmes": "princeton-mfin"}}),
}


@pytest.mark.parametrize("route", NEW_WRITE_ROUTES)
def test_recommender_write_routes_refuse_a_foreign_origin(
    shared: tuple[TestClient, Path], route: str
) -> None:
    bare, data_dir = shared
    url, kwargs = NEW_WRITE_ROUTES[route]
    before = tree_digest(data_dir)
    res = bare.post(url, headers={"Origin": "https://evil.example"}, **kwargs)
    assert res.status_code == 403
    assert tree_digest(data_dir) == before
