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


NO_JS_PAGES = [
    "/",
    "/courses",
    "/courses/import",
    "/courses/deleted",
    "/courses/merge",
    "/recommenders",
    "/recommenders/new",
    "/recommenders/from-courses",  # #102
    "/recommenders/emails",  # #104
    "/programmes/new",
    "/programmes/princeton-mfin",
    "/programmes/booth-mif/standard",  # #97
    "/usage",  # ui review round 5 note: the 320 px guard reuses this list
    "/settings",
]


def test_without_js_no_page_scrolls_sideways_at_390px(browser, live_server: str) -> None:  # noqa: ANN001
    """ui review round 3, W6: without JS the header's zone form shows an Apply button (<noscript>), which
    pushed every page 21 px wide at 390 px. The form wraps instead."""
    context = browser.new_context(java_script_enabled=False, viewport={"width": 390, "height": 844})
    page = context.new_page()
    try:
        wide = []
        for route in NO_JS_PAGES:
            page.goto(live_server + route, wait_until="load")  # without JS, DOMContentLoaded can beat the CSS
            width = page.evaluate("document.documentElement.scrollWidth")
            if width > 390:
                wide.append(f"{route}: {width}")
        assert wide == []
    finally:
        context.close()


@pytest.mark.parametrize("js", [True, False])
def test_no_page_scrolls_sideways_at_320px(browser, live_server: str, js: bool) -> None:  # noqa: ANN001
    """ui review round 4, N2: 320 CSS px is the WCAG 1.4.10 reflow width; the Recommenders toolbar (four
    buttons since 2c) did not wrap."""
    context = browser.new_context(java_script_enabled=js, viewport={"width": 320, "height": 800})
    page = context.new_page()
    try:
        wide = []
        for route in NO_JS_PAGES:
            page.goto(live_server + route, wait_until="load")
            width = page.evaluate("document.documentElement.scrollWidth")
            if width > 320:
                wide.append(f"{route}: {width}")
        assert wide == []
    finally:
        context.close()
