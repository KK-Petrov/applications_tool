"""Smoke e2e: the app shell loads in a real browser and the sidebar navigates."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def test_dashboard_loads_with_sidebar(page: Page, live_server: str) -> None:
    page.goto(live_server + "/", wait_until="domcontentloaded")
    expect(page).to_have_title("Dashboard · Application Hub")
    nav = page.get_by_role("navigation", name="Main")
    for label in ("Dashboard", "Programmes", "Recommenders", "Essays and CV", "Biography bank", "Settings"):
        expect(nav.get_by_role("link", name=label)).to_be_visible()
    expect(page.get_by_role("heading", level=1)).to_have_text("Dashboard")


def test_sidebar_link_opens_page(page: Page, live_server: str) -> None:
    page.goto(live_server + "/", wait_until="domcontentloaded")
    page.get_by_role("navigation", name="Main").get_by_role("link", name="Recommenders").click()
    page.wait_for_url("**/recommenders")
    expect(page.get_by_role("heading", level=1)).to_have_text("Recommenders")
