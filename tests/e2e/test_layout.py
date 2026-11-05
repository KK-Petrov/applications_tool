"""Layout (docs/03 §3): the sidebar is 220 px wide on desktop, stacks below 760 px, no sideways scroll."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

pytestmark = pytest.mark.e2e

SIDEBAR_PX = 220  # docs/03 §3 and tokens.css --sidebar-width


@pytest.mark.parametrize("width", [761, 1024, 1280, 1600])
def test_sidebar_is_220px_and_page_never_scrolls_sideways(page: Page, live_server: str, width: int) -> None:
    page.set_viewport_size({"width": width, "height": 800})
    for route in ("/", "/recommenders"):
        page.goto(live_server + route, wait_until="domcontentloaded")
        sidebar = page.locator(".sidebar").bounding_box()
        assert sidebar is not None and round(sidebar["width"]) == SIDEBAR_PX
        assert page.evaluate("document.documentElement.scrollWidth") <= width


def test_sidebar_stacks_on_narrow_screens(page: Page, live_server: str) -> None:
    page.set_viewport_size({"width": 760, "height": 800})
    page.goto(live_server + "/", wait_until="domcontentloaded")
    sidebar = page.locator(".sidebar").bounding_box()
    main = page.locator("main").bounding_box()
    assert sidebar is not None and main is not None
    assert round(sidebar["width"]) == 760
    assert main["y"] >= sidebar["y"] + sidebar["height"] - 1
    assert page.evaluate("document.documentElement.scrollWidth") <= 760
