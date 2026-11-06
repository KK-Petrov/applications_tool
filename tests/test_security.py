"""Cross-site request and DNS-rebinding protection for the local app (DECISIONS #26).

- Host must be 127.0.0.1:<port> or localhost:<port> (the port the server is bound to), else 400.
- Writes (POST/PUT/PATCH/DELETE): an Origin header must be the app's own origin, else 403; without an
  Origin, a Referer from the app's own origin is required, else 403. A rejected request writes nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hub.app import create_app

from helpers import BASE, FIXED_NOW, tree_digest

VERIFY = "/programmes/princeton-mfin/verify"


@pytest.fixture
def bare(config) -> TestClient:
    """A client on the app's own host that sends no Origin or Referer unless a test adds one."""
    return TestClient(create_app(config, clock=lambda: FIXED_NOW), base_url=BASE, follow_redirects=False)


def _princeton(data_dir: Path) -> bytes:
    return (data_dir / "programmes" / "princeton-mfin.yaml").read_bytes()


@pytest.mark.parametrize(
    "origin", ["https://evil.example", "null", "http://127.0.0.1:9999", "http://127.0.0.1"]
)
def test_foreign_origin_is_refused_and_nothing_written(bare: TestClient, data_dir: Path, origin: str) -> None:
    before = tree_digest(data_dir)
    res = bare.post(VERIFY, headers={"Origin": origin})
    assert res.status_code == 403
    assert tree_digest(data_dir) == before


def test_no_origin_and_no_referer_is_refused(bare: TestClient, data_dir: Path) -> None:
    before = _princeton(data_dir)
    assert bare.post(VERIFY).status_code == 403
    assert _princeton(data_dir) == before


@pytest.mark.parametrize(
    "referer", ["https://evil.example/programmes/princeton-mfin", "http://127.0.0.1:8765.evil.example/"]
)
def test_foreign_referer_is_refused(bare: TestClient, data_dir: Path, referer: str) -> None:
    before = _princeton(data_dir)
    assert bare.post(VERIFY, headers={"Referer": referer}).status_code == 403
    assert _princeton(data_dir) == before


def test_foreign_origin_wins_over_own_referer(bare: TestClient, data_dir: Path) -> None:
    before = _princeton(data_dir)
    res = bare.post(VERIFY, headers={"Origin": "https://evil.example", "Referer": BASE + "/"})
    assert res.status_code == 403
    assert _princeton(data_dir) == before


@pytest.mark.parametrize(
    "host", ["evil.example", "127.0.0.1.evil.example:8765", "127.0.0.1:9999", "192.168.1.5:8765"]
)
def test_wrong_host_is_rejected(bare: TestClient, host: str) -> None:
    assert bare.get("/", headers={"Host": host}).status_code == 400


def test_wrong_host_write_is_rejected_and_nothing_written(bare: TestClient, data_dir: Path) -> None:
    before = tree_digest(data_dir)
    res = bare.post(VERIFY, headers={"Host": "evil.example", "Origin": "http://evil.example"})
    assert res.status_code == 400
    assert tree_digest(data_dir) == before


def test_rejection_page_shows_no_data(bare: TestClient) -> None:
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


def test_gets_need_no_origin(bare: TestClient) -> None:
    for route in ("/", "/programmes", "/programmes/princeton-mfin", "/static/css/tokens.css"):
        assert bare.get(route).status_code == 200, route
