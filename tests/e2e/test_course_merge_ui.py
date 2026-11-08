"""Merge duplicate courses, select-and-delete or -merge on the list, Merge all, and "Merge into" at import,
end to end (DECISIONS #95; docs/03 §4.8); axe on every page and state."""

from __future__ import annotations

from pathlib import Path

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import Page, expect
from ruamel.yaml import YAML

from helpers import write_recording

pytestmark = pytest.mark.e2e
AXE = Axe()

COURSES = """\
courses:
  - id: corporate-finance-feb12003
    name: Corporate Finance
    code: FEB12003
    institution: Example School of Economics
    grade: "9,5"
    credits: 6.0
    term: 2022-2023 block 2
    lecturers:
      - Dr. L. Ødegaard
    description: ''
    source_document: transcript-a.txt
    confirmed: true
  - id: corporate-finance-feb12003-2
    name: Corporate Finance
    code: FEB12003
    institution: ''
    grade: "9,5"
    credits: 6.0
    term: 2023-2024 block 2
    lecturers: []
    description: Capital structure.
    source_document: transcript-b.txt
    confirmed: true
  - id: marketing-feb12002
    name: Marketing
    code: FEB12002
    institution: Example School of Economics
    grade: "6,5"
    credits: 4.0
    term: 2022-2023 block 1
    lecturers:
      - drs. P. Jansen
    description: ''
    source_document: transcript-a.txt
    confirmed: true
  - id: investments-feb12006
    name: Investments
    code: FEB12006
    institution: Example School of Economics
    grade: "9,0"
    credits: 6.0
    term: 2022-2023 block 3
    lecturers: []
    description: ''
    source_document: transcript-a.txt
    confirmed: true
"""


def _serious(page: Page) -> list[str]:
    results = AXE.run(page)
    return [
        f"{v['id']} ({v['impact']}): {[', '.join(n['target']) for n in v['nodes']][:5]}"
        for v in results.response["violations"]
        if v.get("impact") in {"serious", "critical"}
    ]


def _stored(data_dir: Path) -> dict[str, dict]:
    data = YAML(typ="safe").load((data_dir / "courses.yaml").read_text(encoding="utf-8"))
    return {c["id"]: c for c in data["courses"]}


@pytest.fixture
def with_courses(data_dir: Path) -> Path:
    path = data_dir / "courses.yaml"
    path.write_text(COURSES, encoding="utf-8", newline="\n")
    return path


def test_merge_a_group_picking_the_term(
    page: Page, live_server: str, data_dir: Path, with_courses: Path
) -> None:
    page.goto(f"{live_server}/courses")
    page.get_by_role("link", name="Merge duplicates").click()
    expect(page.get_by_text("Term: the copies differ; pick one")).to_be_visible()
    assert _serious(page) == []
    page.get_by_label("2023-2024 block 2").check()
    page.get_by_role("button", name="Merge these copies").click()
    expect(page.locator("#courses-notice")).to_contain_text("Merged 2 copies into Corporate Finance")
    assert _serious(page) == []
    stored = _stored(data_dir)
    assert stored["corporate-finance-feb12003"]["term"] == "2023-2024 block 2"
    assert stored["corporate-finance-feb12003-2"]["merged_into"] == "corporate-finance-feb12003"


def test_select_and_delete_then_merge_selected(
    page: Page, live_server: str, data_dir: Path, with_courses: Path
) -> None:
    page.goto(f"{live_server}/courses")
    page.get_by_label("Select Investments (FEB12006)").check()
    page.get_by_label("Select Marketing (FEB12002)").check()
    assert _serious(page) == []
    page.get_by_role("button", name="Delete selected").click()
    expect(page.locator("#courses-notice")).to_contain_text("2 courses deleted")
    assert _stored(data_dir)["investments-feb12006"]["deleted"] is True
    page.get_by_role("button", name="Merge selected").click()
    expect(page.locator("#courses-error")).to_contain_text("Select at least two courses to merge")
    assert _serious(page) == []


def test_merge_all_duplicates(page: Page, live_server: str, data_dir: Path, with_courses: Path) -> None:
    page.goto(f"{live_server}/courses")
    page.get_by_role("button", name="Merge all duplicates").click()
    expect(page.locator("#courses-notice")).to_contain_text("1 group needs your choice")  # the term differs
    assert _serious(page) == []


def test_import_offers_merge_into_a_stored_course(
    page: Page, live_server: str, data_dir: Path, with_courses: Path, tmp_path: Path, monkeypatch
) -> None:
    row = {
        "name": "Marketing",
        "code": "FEB12002",
        "institution": "Example School of Economics",
        "grade": "6,5",
        "credits": 4.0,
        "term": "2022-2023 block 1",
        "lecturers": ["drs. P. Jansen"],
        "description": "Consumer behaviour.",
    }
    recording = write_recording(tmp_path, {"courses": [row], "warnings": []}, "courses.json")
    monkeypatch.setenv("FAKE_CLAUDE_RECORDING", str(recording))
    page.goto(f"{live_server}/courses/import", wait_until="domcontentloaded")
    page.get_by_label("Or paste the text").fill(
        "Courses at Example School of Economics\n"
        "FEB12002\tMarketing\t6,5\t4\t2022-2023 block 1\tdrs. P. Jansen\n"
        "Description: Consumer behaviour.\n"
    )
    page.get_by_role("button", name="Read courses").click()
    page.get_by_role("button", name="Send to Claude").click()
    merge = page.get_by_role("button", name="Merge into Marketing")
    expect(merge).to_be_visible()
    assert _serious(page) == []
    merge.click()
    expect(page.get_by_text("Merged into Marketing").first).to_be_visible()
    assert _serious(page) == []
    assert _stored(data_dir)["marketing-feb12002"]["description"] == "Consumer behaviour."
