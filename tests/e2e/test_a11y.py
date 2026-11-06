"""C-1-05: no serious or critical axe violations on any page (docs/03 §7, WCAG 2.2 AA).

``axe_serious_violations`` is the number of serious + critical violations over every page below; the
threshold is 0. Each failure message lists the rule, impact and the offending elements.
"""

from __future__ import annotations

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import Page

pytestmark = pytest.mark.e2e

PAGES = [
    "/",
    "/programmes",
    "/programmes/princeton-mfin",
    "/programmes/mit-sloan-mfin",
    "/programmes/booth-mif",
    "/programmes/columbia-msfe",
    "/programmes/new",
    "/programmes/princeton-mfin/edit",
    "/recommenders",
    "/recommenders?selected=jansen",
    "/recommenders/new",
    "/recommenders/jansen/edit",
    "/recommenders/import",
    "/essays",
    "/biography",
    "/settings",
    "/no-such-page",
]
SERIOUS = {"serious", "critical"}
AXE = Axe()


def _serious(page: Page) -> list[str]:
    results = AXE.run(page)
    out = []
    for v in results.response["violations"]:
        if v.get("impact") in SERIOUS:
            targets = [", ".join(n["target"]) for n in v["nodes"]][:5]
            out.append(f"{v['id']} ({v['impact']}): {v['help']} → {targets}")
    return out


@pytest.mark.parametrize("path", PAGES)
def test_no_serious_violations(page: Page, live_server: str, path: str) -> None:
    page.goto(live_server + path, wait_until="domcontentloaded")
    violations = _serious(page)
    assert not violations, f"axe_serious_violations={len(violations)} on {path}:\n" + "\n".join(violations)


def test_no_serious_violations_in_rotterdam_mode(page: Page, live_server: str) -> None:
    page.goto(live_server + "/", wait_until="domcontentloaded")
    with page.expect_navigation(wait_until="domcontentloaded"):  # HX-Refresh reloads the page
        page.get_by_label("Times in:").select_option("Europe/Amsterdam")
    for path in ("/", "/programmes/princeton-mfin"):
        page.goto(live_server + path, wait_until="domcontentloaded")
        violations = _serious(page)
        assert not violations, f"{path} (Rotterdam):\n" + "\n".join(violations)


def test_no_serious_violations_with_open_forms(page: Page, live_server: str) -> None:
    page.goto(live_server + "/programmes/princeton-mfin", wait_until="domcontentloaded")
    page.locator("#interviews").get_by_role("button", name="Add interview or assessment").click()
    page.locator("#interviews form.interview-form").last.wait_for()
    violations = _serious(page)
    assert not violations, "interview form:\n" + "\n".join(violations)
    page.goto(live_server + "/programmes/new", wait_until="domcontentloaded")
    page.get_by_role("button", name="Add deadline").click()
    page.wait_for_load_state("domcontentloaded")
    violations = _serious(page)
    assert not violations, "programme form with added row:\n" + "\n".join(violations)


def test_no_serious_violations_with_open_status_menu_and_panel(page: Page, live_server: str) -> None:
    page.goto(live_server + "/recommenders", wait_until="domcontentloaded")
    page.get_by_label("Change Princeton MFin status for Prof. Dr. Eva Jansen").click()
    page.get_by_role("button", name="Requested", exact=True).wait_for()
    violations = _serious(page)
    assert not violations, "open status menu:\n" + "\n".join(violations)
    page.goto(live_server + "/recommenders?selected=jansen", wait_until="domcontentloaded")
    page.locator("#panel").get_by_text("Mark as requested").click()
    violations = _serious(page)
    assert not violations, "panel with open checklist:\n" + "\n".join(violations)
