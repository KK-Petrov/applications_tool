"""App shell (Phase 0): sidebar, header, empty pages, DataError banner, design tokens."""

from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hub.app import create_app

from helpers import BASE
from paths import INVALID, REPO_ROOT

PAGES = {
    "/": "Dashboard",
    "/programmes": "Programmes",
    "/recommenders": "Recommenders",
    "/essays": "Essays and CV",
    "/biography": "Biography bank",
    "/settings": "Settings",
}
TEMPLATES = REPO_ROOT / "src" / "hub" / "templates"
STATIC = REPO_ROOT / "src" / "hub" / "static"
FIXED_NOW = datetime(2026, 10, 30, 16, 0, tzinfo=UTC)  # Friday 30 October 2026, 12:00 New York


@pytest.fixture
def client(config) -> TestClient:
    # Phase 1: requests must come from the app's own host (DECISIONS #26), so the client uses BASE.
    return TestClient(create_app(config, clock=lambda: FIXED_NOW), base_url=BASE)


@pytest.mark.parametrize("route", PAGES)
def test_every_sidebar_page_renders(client: TestClient, route: str) -> None:
    res = client.get(route)
    assert res.status_code == 200
    html = res.text
    for label in PAGES.values():
        assert f">{label}<" in html
    assert "Application Hub" in html
    assert f"<h1>{PAGES[route]}</h1>" in html
    assert 'aria-current="page"' in html
    assert "None" not in html and "NaN" not in html and "Traceback" not in html


def test_header_shows_date_and_zone_selector(client: TestClient) -> None:
    html = client.get("/").text
    assert "Friday 30 October 2026" in html
    assert "Times in" in html
    assert "Eastern Time (Charlottesville)" in html
    assert "Central European Time (Rotterdam)" in html
    assert re.search(r"<label[^>]*for=\"display-tz\"", html)


def test_sidebar_footer(client: TestClient) -> None:
    html = client.get("/").text
    assert "Reminders sync to Google Calendar" in html
    assert "Applications" in html
    assert "Daily digest at first login" in html


def test_static_assets_are_served_locally(client: TestClient) -> None:
    html = client.get("/").text
    assert '/static/htmx.min.js"' in html
    assert client.get("/static/htmx.min.js").status_code == 200
    assert client.get("/static/css/tokens.css").status_code == 200
    # Only attributes that make the browser load something count; <a href> links (e.g. deadline source
    # links, C-1-01) load nothing until clicked. Narrowed in Phase 1 with the user's approval (DECISIONS #28).
    loads = re.compile(
        r"<(?:script|img|iframe|source|video|audio|embed)\b[^>]*\s(?:src|srcset)=\"(https?://[^\"]+)\""
        r"|<link\b[^>]*\shref=\"(https?://[^\"]+)\""
    )
    for page in ("/", "/programmes/princeton-mfin"):
        text = client.get(page).text
        external = [a or b for a, b in loads.findall(text)]
        assert external, "the Google Fonts stylesheet should be found by the pattern"
        assert all(
            u.startswith(("https://fonts.googleapis.com", "https://fonts.gstatic.com")) for u in external
        )
    assert not re.search(r"<script\b[^>]*\ssrc=\"https?://", html)


def test_data_error_banner_names_file_and_field(data_dir: Path, client: TestClient) -> None:
    shutil.copy(INVALID / "programmes" / "missing-tz.yaml", data_dir / "programmes" / "missing-tz.yaml")
    html = client.get("/").text
    assert 'role="alert"' in html
    assert "programmes/missing-tz.yaml" in html
    assert "deadlines[0].tz" in html


def test_no_banner_when_data_is_valid(client: TestClient) -> None:
    assert 'role="alert"' not in client.get("/").text


def test_unknown_page_is_404(client: TestClient) -> None:
    assert client.get("/nope").status_code == 404


TOKENS = [
    "#F6F7F9", "#FFFFFF", "#15181E", "#3F4652", "#5A6170", "#DDE1E7", "#EEF0F3", "#262A33", "#C9CDD5",
    "#1F4FD1", "#163A9C", "#B4480A", "#FFF1E6", "#9A3D06", "#1E6B45", "#E7F4EC", "#1A3E9E", "#E8EEFC",
    "#9B1C1C", "#FBE9E9",
]  # fmt: skip


def test_tokens_css_defines_every_design_token() -> None:
    css = (STATIC / "css" / "tokens.css").read_text(encoding="utf-8").upper()
    missing = [t for t in TOKENS if t not in css]
    assert missing == []
    for value in ("8PX", "6PX", "999PX", "44PX", "14PX", "28PX", "16PX", "IBM PLEX SANS", "IBM PLEX MONO"):
        assert value in css


def test_templates_and_app_css_never_hard_code_colours() -> None:
    hex_colour = re.compile(r"#[0-9A-Fa-f]{3}(?:[0-9A-Fa-f]{3})?\b")
    offenders = []
    for path in [*TEMPLATES.rglob("*.html"), *(STATIC / "css").glob("*.css")]:
        if path.name == "tokens.css":
            continue
        text = path.read_text(encoding="utf-8")
        scan = (
            text
            if path.suffix == ".css"
            else " ".join(re.findall(r"style=\"[^\"]*\"|<style.*?</style>", text, re.S))
        )
        offenders += [f"{path.name}: {m}" for m in hex_colour.findall(scan)]
    assert offenders == []
