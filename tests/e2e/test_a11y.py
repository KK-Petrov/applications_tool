"""C-1-05: no serious or critical axe violations on any page (docs/03 §7, WCAG 2.2 AA).

``axe_serious_violations`` is the number of serious + critical violations over every page below; the
threshold is 0. Each failure message lists the rule, impact and the offending elements.
"""

from __future__ import annotations

import shutil

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import Page

from hub.ai import usage

from paths import FIXTURES

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
    "/recommenders?selected=whitfield",  # a stored email in the panel (2b)
    "/recommenders/new",
    "/recommenders/jansen/edit",
    "/recommenders/import",
    "/essays",
    "/biography",
    "/usage",  # empty state (DECISIONS #60)
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


def test_no_serious_violations_with_a_generated_email(page: Page, live_server: str) -> None:
    page.goto(live_server + "/recommenders?selected=jansen", wait_until="domcontentloaded")
    page.locator("#panel").get_by_role("button", name="Generate email").click()
    page.locator("#email-result").wait_for()
    violations = _serious(page)
    assert not violations, "panel with a generated email:\n" + "\n".join(violations)


def test_no_serious_violations_while_generating(
    page: Page, live_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ui re-review: the busy state (up to 120 s) had never been checked; a global dimming rule failed it."""
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "timeout")
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "6")  # holds the busy state; then the call ends with an error
    page.goto(live_server + "/recommenders?selected=jansen", wait_until="domcontentloaded")
    page.locator("#panel").get_by_role("button", name="Generate email").click()
    page.locator('form.generate-form[data-busy="true"]').wait_for()
    violations = _serious(page)
    assert not violations, "panel while generating:\n" + "\n".join(violations)
    page.locator("#panel .email-area .form-error").wait_for(timeout=20000)  # let the request finish


def test_no_serious_violations_on_the_usage_page_with_calls_and_a_bad_line(
    page: Page, live_server: str, config
) -> None:
    """DECISIONS #60: the AI usage page with rows from every source, totals, filters and the error banner."""
    app_log = usage.app_log_path(config)
    app_log.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURES / "usage" / "app.jsonl", app_log)
    shutil.copyfile(FIXTURES / "usage" / "run.jsonl", usage.run_log_path(config))
    for path in ("/usage", "/usage?source=all", "/usage?month=2026-01"):
        page.goto(live_server + path, wait_until="domcontentloaded")
        violations = _serious(page)
        assert not violations, f"{path}:\n" + "\n".join(violations)
    app_log.write_text(app_log.read_text(encoding="utf-8") + "not json\n", encoding="utf-8")
    page.goto(live_server + "/usage", wait_until="domcontentloaded")
    page.get_by_role("alert").first.wait_for()
    violations = _serious(page)
    assert not violations, "usage page with the error banner:\n" + "\n".join(violations)
