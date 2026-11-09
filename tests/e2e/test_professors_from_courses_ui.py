"""Professors from your courses, end to end (DECISIONS #102; docs/03 §4.3, §4.8): from the Recommenders
toolbar, Tick all, Create ticked professors, with and without JavaScript; axe on every state."""

from __future__ import annotations

from pathlib import Path

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import Browser, Page, expect

pytestmark = pytest.mark.e2e
AXE = Axe()
COURSES = """\
courses:
  - id: corporate-finance-feb12003
    name: Corporate Finance
    code: FEB12003
    institution: Example School of Economics
    grade: "9,0"
    credits: 6.0
    lecturers:
      - Dr. L. Ødegaard
    description: ''
    source_document: transcript-a.txt
    confirmed: true
  - id: investments-feb12006
    name: Investments
    code: FEB12006
    institution: Example School of Economics
    grade: "7,5"
    credits: 6.0
    lecturers:
      - L. Odegaard
      - Dr. J. Smit
    description: ''
    source_document: master-profile.txt
    confirmed: true
"""


def _serious(page: Page) -> list[str]:
    results = AXE.run(page)
    return [
        f"{v['id']} ({v['impact']}): {[', '.join(n['target']) for n in v['nodes']][:5]}"
        for v in results.response["violations"]
        if v.get("impact") in {"serious", "critical"}
    ]


@pytest.fixture
def courses(data_dir: Path) -> Path:
    path = data_dir / "courses.yaml"
    path.write_text(COURSES, encoding="utf-8", newline="\n")
    return path


@pytest.mark.parametrize("js", [True, False])
def test_professors_from_your_courses(
    browser: Browser, live_server: str, data_dir: Path, courses: Path, js: bool
) -> None:
    context = browser.new_context(java_script_enabled=js, viewport={"width": 390, "height": 844})
    try:
        page = context.new_page()
        page.goto(f"{live_server}/recommenders")
        page.get_by_role("link", name="Professors from your courses").click()
        expect(page.get_by_role("heading", name="Professors from your courses")).to_be_visible()
        boxes = page.locator("#professors-preview input[name=pick]")
        expect(boxes).to_have_count(2)  # Ødegaard (two documents, once) and Smit
        for i in range(2):
            expect(boxes.nth(i)).not_to_be_checked()  # nothing ticked by default
        if js:
            assert _serious(page) == []
        page.get_by_role("button", name="Tick all").click()
        for i in range(2):
            expect(page.locator("#professors-preview input[name=pick]").nth(i)).to_be_checked()
        assert page.evaluate("document.documentElement.scrollWidth") <= 390
        page.get_by_role("button", name="Create ticked professors").click()
        expect(page.get_by_text("Created 2 professors")).to_be_visible()
        assert (data_dir / "professors" / "odegaard.yaml").exists()
        if js:
            assert _serious(page) == []
    finally:
        context.close()
