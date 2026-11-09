"""Sheet Grade column from linked courses (Phase 3 pre-build, user request; docs/03 §4.3).

When a professor's own ``grade`` is empty, the sheet's Grade cell and the panel's Grade show the grades of the
professor's linked courses, in the order the professor links them (the Course column's order), several joined
with " · "; a linked course without a grade adds nothing. The professor's own grade wins. Display only: no
data file changes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from helpers import FormClient, tree_digest

HX = {"HX-Request": "true"}
COURSES = """\
courses:
  - id: corporate-finance
    name: Corporate Finance
    grade: "9,0"
    lecturers: [Dr. S. Whitfield]
    description: ''
    confirmed: true
  - id: investments
    name: Investments
    grade: "7,5"
    lecturers: [Dr. S. Whitfield]
    description: ''
    confirmed: true
  - id: ethics
    name: Ethics
    grade: null
    lecturers: [Dr. S. Whitfield]
    description: ''
    confirmed: true
"""


@pytest.fixture
def courses(data_dir: Path) -> Path:
    path = data_dir / "courses.yaml"
    path.write_text(COURSES, encoding="utf-8", newline="\n")
    return path


def _set(data_dir: Path, pid: str, **fields: object) -> None:
    yaml = YAML()
    path = data_dir / "professors" / f"{pid}.yaml"
    data = yaml.load(path.read_text(encoding="utf-8"))
    data.update(fields)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        yaml.dump(data, fh)


def _grade_cell(html: str, pid: str) -> str:
    row = re.search(rf'<tr id="row-{pid}".*?</tr>', html, re.S).group(0)
    return re.search(r'<td class="grade">(.*?)</td>', row, re.S).group(1).strip()


def _panel_grade(client: FormClient, pid: str) -> str | None:
    html = client.get(f"/recommenders/{pid}/panel", headers=HX).text
    found = re.search(r"<dt>Grade</dt><dd>(.*?)</dd>", html, re.S)
    return found.group(1).strip() if found else None


@pytest.mark.parametrize(
    ("course_ids", "shown"),
    [
        ([], ""),  # no linked course, no own grade
        (["investments"], "7,5"),
        (["investments", "corporate-finance"], "7,5 · 9,0"),  # the professor's link order, not the file's
        (["ethics", "corporate-finance"], "9,0"),  # a course without a grade adds nothing
        (["ethics"], ""),
    ],
)
def test_empty_own_grade_shows_linked_course_grades(
    client: FormClient, data_dir: Path, courses: Path, course_ids: list[str], shown: str
) -> None:
    _set(data_dir, "whitfield", course_ids=course_ids, grade=None)
    assert _grade_cell(client.get("/recommenders").text, "whitfield") == shown
    assert _panel_grade(client, "whitfield") == (shown or None)  # no Grade line when there is nothing


def test_own_grade_wins_over_linked_courses(client: FormClient, data_dir: Path, courses: Path) -> None:
    _set(data_dir, "jansen", course_ids=["corporate-finance", "investments"])  # grade "8.5 / 10" stays
    assert _grade_cell(client.get("/recommenders").text, "jansen") == "8.5 / 10"
    assert _panel_grade(client, "jansen") == "8.5 / 10"


def test_an_empty_string_own_grade_counts_as_empty(client: FormClient, data_dir: Path, courses: Path) -> None:
    _set(data_dir, "whitfield", course_ids=["corporate-finance"], grade="")
    assert _grade_cell(client.get("/recommenders").text, "whitfield") == "9,0"


def test_showing_course_grades_writes_nothing(client: FormClient, data_dir: Path, courses: Path) -> None:
    _set(data_dir, "whitfield", course_ids=["investments", "corporate-finance"], grade=None)
    before = tree_digest(data_dir)
    client.get("/recommenders")
    client.get("/recommenders?selected=whitfield")
    client.get("/recommenders/whitfield/panel", headers=HX)
    assert tree_digest(data_dir) == before
