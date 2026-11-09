"""Add standard steps and documents, end to end (DECISIONS #97; docs/03 §4.2): from the programme page to
the preview, untick a row, save, see what was added; with and without JavaScript; axe on every state; and
Edit programme saying which step lost its link (user addition to Q2)."""

from __future__ import annotations

from pathlib import Path

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import Browser, Page, expect

pytestmark = pytest.mark.e2e
AXE = Axe()
PID = "probe-std"
PROBE = """\
# Probe programme for the standard items (invented)
id: probe-std
name: Probe Institute Master in Quantitative Finance
short_name: Probe MQF
school: Probe Institute
deadlines:
  - id: main
    label: Application deadline
    due_local: 2027-02-01T17:00
    tz: America/New_York
    source_url: https://example.com/apply
requirements:
  - id: transcripts
    label: Official transcripts
  - id: tests
    label: GMAT or GRE score
  - id: letters
    label: Two letters of recommendation
  - id: fee
    label: Application fee
"""


def _serious(page: Page) -> list[str]:
    results = AXE.run(page)
    return [
        f"{v['id']} ({v['impact']}): {[', '.join(n['target']) for n in v['nodes']][:5]}"
        for v in results.response["violations"]
        if v.get("impact") in {"serious", "critical"}
    ]


@pytest.fixture
def probe(data_dir: Path) -> Path:
    path = data_dir / "programmes" / f"{PID}.yaml"
    path.write_text(PROBE, encoding="utf-8", newline="\n")
    return path


def _add(page: Page, live_server: str) -> None:
    page.goto(f"{live_server}/programmes/{PID}")
    page.get_by_role("link", name="Add standard steps and documents").click()
    expect(page.get_by_role("heading", name="Add standard steps and documents")).to_be_visible()
    assert _serious(page) == []
    page.get_by_label("GRE score report").uncheck()
    page.get_by_role("button", name="Add ticked items").click()
    notice = page.locator("#programme-notice")
    expect(notice).to_have_text("Added 5 steps and 2 documents.")
    expect(notice).to_be_focused()
    assert _serious(page) == []
    expect(page.locator("#steps")).to_contain_text("For: Application fee")
    expect(page.locator("#documents")).to_contain_text("Official transcript")
    expect(page.locator("#documents")).not_to_contain_text("GRE score report")


def test_add_standard_items(page: Page, live_server: str, probe: Path) -> None:
    _add(page, live_server)


def test_add_standard_items_without_javascript(browser: Browser, live_server: str, probe: Path) -> None:
    context = browser.new_context(java_script_enabled=False, viewport={"width": 390, "height": 844})
    try:
        page = context.new_page()
        page.goto(f"{live_server}/programmes/{PID}/standard")
        page.get_by_label("GRE score report").uncheck()
        page.get_by_role("button", name="Add ticked items").click()
        expect(page.locator("#programme-notice")).to_have_text("Added 5 steps and 2 documents.")
        assert page.evaluate("document.documentElement.scrollWidth") <= 390
    finally:
        context.close()


def test_edit_says_which_step_lost_its_link(page: Page, live_server: str, probe: Path) -> None:
    _add(page, live_server)
    page.goto(f"{live_server}/programmes/{PID}/edit")
    page.locator("#requirements-3-remove").check()  # Application fee, the fourth requirement
    page.get_by_role("button", name="Save programme").click()
    notice = page.locator("#programme-notice")
    expect(notice).to_have_text("Pay the application fee is no longer linked to Application fee.")
    expect(notice).to_be_focused()
    assert _serious(page) == []
