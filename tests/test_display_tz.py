"""US-2 at the HTTP level: the header zone selector saves settings.display_tz (docs/03 §1, docs/02 §1)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from helpers import BASE, tree_digest


def _settings(data_dir: Path) -> str:
    return (data_dir / "settings.yaml").read_text(encoding="utf-8")


def test_plain_post_saves_and_redirects_back(client: TestClient, data_dir: Path) -> None:
    res = client.post(
        "/display-tz",
        data={"display_tz": "Europe/Amsterdam"},
        headers={"Referer": BASE + "/programmes/princeton-mfin"},
    )
    assert res.status_code == 303
    assert res.headers["location"] == "/programmes/princeton-mfin"
    assert "display_tz: Europe/Amsterdam" in _settings(data_dir)
    assert "# Test fixture: invented owner." in _settings(data_dir)  # comments survive


def test_redirect_never_leaves_the_app(client: TestClient) -> None:
    res = client.post(
        "/display-tz", data={"display_tz": "Europe/Amsterdam"}, headers={"Referer": BASE + "//evil.example/x"}
    )
    assert res.status_code == 303
    assert res.headers["location"].startswith("/") and not res.headers["location"].startswith("//")


def test_htmx_post_asks_for_a_refresh(client: TestClient, data_dir: Path) -> None:
    res = client.post("/display-tz", data={"display_tz": "Europe/Amsterdam"}, headers={"HX-Request": "true"})
    assert res.status_code == 204
    assert res.headers.get("HX-Refresh") == "true"


def test_every_time_follows_the_display_zone(client: TestClient) -> None:
    eastern = client.get("/programmes/princeton-mfin").text
    assert "15 Dec 2026, 11:59 pm ET" in eastern
    client.post("/display-tz", data={"display_tz": "Europe/Amsterdam"})
    rotterdam = client.get("/programmes/princeton-mfin").text
    assert "16 Dec 2026, 05:59 CET (Rotterdam)" in rotterdam
    assert "15 Dec 2026, 11:59 pm ET" in rotterdam  # school-zone original on programme pages
    dashboard = client.get("/").text
    assert "05:59 CET (Rotterdam)" in dashboard and "11:59 pm ET" not in dashboard
    assert 'value="Europe/Amsterdam" selected' in dashboard


def test_unknown_zone_is_refused_and_nothing_written(client: TestClient, data_dir: Path) -> None:
    before = tree_digest(data_dir)
    for zone in ("Asia/Tokyo", "Not/AZone", ""):
        assert client.post("/display-tz", data={"display_tz": zone}).status_code == 400
    assert tree_digest(data_dir) == before


def test_same_zone_writes_nothing(client: TestClient, data_dir: Path) -> None:
    before = tree_digest(data_dir)
    client.post("/display-tz", data={"display_tz": "America/New_York"})
    assert tree_digest(data_dir) == before
