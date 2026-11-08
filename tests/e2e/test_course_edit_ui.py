"""Edit, delete and restore courses end to end (DECISIONS #94; docs/03 §4.8); axe on every page and state."""

from __future__ import annotations

from pathlib import Path

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import Page, expect

from hub.store import Store

pytestmark = pytest.mark.e2e
AXE = Axe()

COURSES = """\
courses:
  - id: investments-feb12006
    name: Investments
    code: FEB12006
    institution: Example School of Economics
    grade: "9,0"
    credits: 6.0
    term: 2022-2023 block 3
    lecturers:
      - Prof. dr. ir. B. ter Horst
    description: ''
    source_document: transcript-2026-11-07.txt
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
def with_course(data_dir: Path) -> Path:
    path = data_dir / "courses.yaml"
    path.write_text(COURSES, encoding="utf-8", newline="\n")
    return path


def test_edit_a_course_and_see_it_marked_as_yours(page: Page, live_server: str, with_course: Path) -> None:
    page.goto(f"{live_server}/courses")
    assert _serious(page) == []
    page.get_by_role("link", name="Edit Investments").click()
    expect(page.get_by_label("Description")).to_be_visible()
    expect(page.locator("#transcript-note")).to_contain_text("came from your transcript")
    assert _serious(page) == []
    page.get_by_label("Description").fill("Portfolio choice and asset pricing.")
    page.get_by_label("Grade").fill("9,5")
    page.get_by_role("button", name="Save").click()
    expect(page.locator("#courses-notice")).to_contain_text("Saved Investments.")
    expect(page.locator("#courses-notice")).to_contain_text("no longer as your transcript gives")
    expect(page.get_by_text("edited by you: grade, description")).to_be_visible()
    assert _serious(page) == []


def test_an_invalid_save_shows_what_to_fix(page: Page, live_server: str, with_course: Path) -> None:
    before = with_course.read_bytes()
    page.goto(f"{live_server}/courses/investments-feb12006/edit")
    page.get_by_label("Credits").fill("six")
    page.get_by_role("button", name="Save").click()
    expect(page.locator("#form-errors")).to_contain_text("Credits must be a number")
    expect(page.get_by_label("Credits")).to_have_attribute("aria-invalid", "true")
    assert _serious(page) == []
    assert with_course.read_bytes() == before


def test_delete_is_refused_while_linked_then_unlink_and_delete(
    page: Page, live_server: str, with_course: Path, config
) -> None:
    store = Store.from_config(config)
    prof = next(p for p in store.load_all().professors if p.id == "jansen")
    store.save_professor(prof.model_copy(update={"course_ids": ["investments-feb12006"]}))
    page.goto(f"{live_server}/courses/investments-feb12006/edit")
    page.get_by_role("button", name="Delete course").click()
    expect(page.get_by_role("heading", name="Investments was not deleted")).to_be_visible()
    expect(page.get_by_role("link", name=prof.name)).to_be_visible()
    assert _serious(page) == []
    page.get_by_role("button", name="Unlink from these professors and delete").click()
    expect(page.locator("#courses-notice")).to_contain_text("Course deleted")
    expect(page.get_by_role("link", name="Edit Investments")).to_have_count(0)
    unlinked = next(p for p in store.load_all().professors if p.id == "jansen")
    assert unlinked.course_ids == []


def test_bring_a_deleted_course_back(page: Page, live_server: str, with_course: Path) -> None:
    page.goto(f"{live_server}/courses/investments-feb12006/edit")
    page.get_by_role("button", name="Delete course").click()
    page.get_by_role("link", name="Deleted courses").click()
    assert _serious(page) == []
    page.get_by_label("Investments (FEB12006)", exact=False).check()
    page.get_by_role("button", name="Bring back").click()
    expect(page.locator("#courses-notice")).to_contain_text("Restored 1 course.")
    expect(page.get_by_role("link", name="Edit Investments")).to_be_visible()
    assert _serious(page) == []
