"""Professors from your courses (user request, DECISIONS #102; docs/03 §4.3, §4.8): the per-document "Create
professors" step (#64, US-20) for every active course at once, from the Recommenders toolbar.

Checked, fast and through the routes:
- every lecturer of the active courses once (surname + initials, case- and diacritic-insensitive), with all
  their courses and the owner's grades, best grade first; deleted courses left out;
- a lecturer matching an existing professor offers "Link to <name>" instead of a new record;
- nothing ticked by default; "Tick all" ticks every Create and every exact-match Link (never a "same surname
  only" link), "Clear all" clears; neither writes anything;
- nothing is written before Create; Create writes the ticked lecturers in one batch through
  ``Store.create_professor`` (email empty, title from the name, course links), adds course links for Link,
  and reports the counts; nothing ticked or an invalid choice is refused with nothing written; a failure part
  way reports how many were created.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hub.store import DataError, Store

from helpers import FormClient, form_fields, tree_digest

URL = "/recommenders/from-courses"
FORM = "professors-preview"

COURSES = """\
courses:
  - id: corporate-finance-feb12003
    name: Corporate Finance
    code: FEB12003
    institution: Example School of Economics
    grade: "9,0"
    credits: 6.0
    term: 2022-2023 block 2
    lecturers:
      - Dr. L. Ødegaard
      - Dr. E. Jansen
    description: ''
    source_document: transcript-a.txt
    confirmed: true
  - id: investments-feb12006
    name: Investments
    code: FEB12006
    institution: Example School of Economics
    grade: "7,5"
    credits: 6.0
    term: 2023-2024 block 1
    lecturers:
      - L. Odegaard
    description: ''
    source_document: master-profile.txt
    confirmed: true
  - id: marketing-feb12002
    name: Marketing
    code: FEB12002
    institution: Example School of Economics
    grade: "6,5"
    credits: 4.0
    term: 2022-2023 block 1
    lecturers:
      - Dr. J. Smit
      - Dr. Whitfield
    description: ''
    source_document: transcript-a.txt
    confirmed: true
  - id: ethics-feb10001
    name: Ethics
    code: FEB10001
    grade: "8,0"
    credits: 2.0
    lecturers:
      - Prof. K. Deleted
    description: ''
    source_document: transcript-a.txt
    confirmed: true
    deleted: true
"""
CF, INV, MKT = "corporate-finance-feb12003", "investments-feb12006", "marketing-feb12002"


@pytest.fixture
def courses(data_dir: Path) -> Path:
    path = data_dir / "courses.yaml"
    path.write_text(COURSES, encoding="utf-8", newline="\n")
    return path


def _page(client: FormClient, **data: str) -> str:
    res = client.get(URL)
    assert res.status_code == 200, res.text[-400:]
    return res.text


def _picks(html: str) -> list[str]:
    return re.findall(r'name="pick" value="([^"]+)"', html)


def _ticked(html: str) -> list[str]:
    return [v for k, v in form_fields(html, FORM) if k == "pick"]


def test_the_recommenders_toolbar_links_to_it(client: FormClient) -> None:
    html = client.get("/recommenders").text
    assert f'href="{URL}"' in html and "Professors from your courses" in html


def test_every_lecturer_once_across_documents_best_grade_first(client: FormClient, courses: Path) -> None:
    html = _page(client)
    legends = re.findall(r"<legend>([^<]+)</legend>", html)
    # best grade first, ties by surname (#64 order): Jansen and Ødegaard both 9,0
    assert legends == ["Dr. E. Jansen", "Dr. L. Ødegaard", "Dr. J. Smit", "Dr. Whitfield"]
    odegaard = html[html.index("Dr. L. Ødegaard") : html.index("Dr. J. Smit")]
    assert "Corporate Finance" in odegaard and "Investments" in odegaard  # both documents, one lecturer
    assert "Also written as: L. Odegaard" in odegaard
    assert "Prof. K. Deleted" not in html  # a deleted course's lecturer


def test_a_matching_professor_is_offered_for_linking(client: FormClient, courses: Path) -> None:
    picks = _picks(_page(client))
    assert any(p.startswith("link:") and p.endswith(":jansen") for p in picks)  # Dr. E. Jansen, exact
    assert any(p.startswith("link:") and p.endswith(":whitfield") for p in picks)  # Dr. Whitfield, surname
    assert not any(p.startswith("create:") and "jansen" in p for p in picks)


def test_nothing_is_ticked_and_nothing_written_by_the_preview(
    client: FormClient, data_dir: Path, courses: Path
) -> None:
    before = tree_digest(data_dir)
    html = _page(client)
    assert _ticked(html) == [] and len(_picks(html)) == 4
    assert tree_digest(data_dir) == before


def test_tick_all_and_clear_all_write_nothing(client: FormClient, data_dir: Path, courses: Path) -> None:
    before = tree_digest(data_dir)
    html = client.post(URL, data={"select": "all"}).text
    ticked = _ticked(html)
    assert sorted(p.split(":")[0] for p in ticked) == ["create", "create", "link"]
    assert not any(p.endswith(":whitfield") for p in ticked)  # "same surname only" stays for the owner
    assert _ticked(client.post(URL, data=[*form_fields(html, FORM), ("select", "none")]).text) == []
    assert tree_digest(data_dir) == before


def test_create_writes_the_ticked_lecturers_in_one_batch(
    client: FormClient, config, data_dir: Path, courses: Path
) -> None:
    html = client.post(URL, data={"select": "all"}).text
    res = client.post(URL, data=[*form_fields(html, FORM), ("step", "create")])
    assert res.status_code == 303 and res.headers["location"] == "/recommenders?created=2&linked=1"
    profs = {p.id: p for p in Store.from_config(config).load_all().professors}
    assert profs["odegaard"].course_ids == [CF, INV] and profs["odegaard"].email in (None, "")
    assert profs["odegaard"].title == "Dr." and profs["odegaard"].name == "Dr. L. Ødegaard"
    assert profs["smit"].course_ids == [MKT]
    assert CF in profs["jansen"].course_ids  # linked, not duplicated
    assert "Created 2 professors and linked 1 lecturer" in client.get(res.headers["location"]).text


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([("step", "create")], "Tick at least one lecturer"),
        ([("step", "create"), ("pick", "create:no-such-lecturer")], "no longer"),
        ([("step", "create"), ("pick", "evil:x")], "not valid"),
    ],
)
def test_refusals_write_nothing(
    client: FormClient, data_dir: Path, courses: Path, data: list, message: str
) -> None:
    before = tree_digest(data_dir)
    res = client.post(URL, data=data)
    assert res.status_code == 409 and message in res.text and "Nothing was saved" in res.text
    assert tree_digest(data_dir) == before


def test_a_failure_part_way_reports_the_counts(
    client: FormClient, data_dir: Path, courses: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = Store.create_professor
    calls: list[str] = []

    def failing_second(self, professor):  # noqa: ANN001, ANN202
        calls.append(professor.id)
        if len(calls) == 2:
            raise DataError(f"professors/{professor.id}.yaml", None, "could not be written: disk full")
        return real(self, professor)

    monkeypatch.setattr(Store, "create_professor", failing_second)
    html = client.post(URL, data={"select": "all"}).text
    res = client.post(URL, data=[*form_fields(html, FORM), ("step", "create")])
    assert res.status_code == 409
    assert "1 of 2 professors were created, then it stopped" in res.text
    assert len(list((data_dir / "professors").glob("*.yaml"))) == 3  # jansen, whitfield + the first


# ---- data-integrity Phase 3 r1 F1: one choice per lecturer, whatever the script (DECISIONS #105) -----------

DISTINCT = """\
courses:
  - id: finance-a
    name: Finance A
    grade: "8,0"
    lecturers: [Prof. I. Иванов]
    description: ''
    confirmed: true
  - id: finance-b
    name: Finance B
    grade: "7,0"
    lecturers: [Prof. P. Петров]
    description: ''
    confirmed: true
  - id: stats-a
    name: Statistics A
    grade: "6,0"
    lecturers: [Dr. A. Smith]
    description: ''
    confirmed: true
  - id: stats-b
    name: Statistics B
    grade: "6,0"
    lecturers: [Dr. A. Smith.]
    description: ''
    confirmed: true
"""
CREATE_ROW = re.compile(r'value="(create:[^"]+)"[^>]*><label for="[^"]+">Create (.+?) <span')


@pytest.fixture
def distinct(data_dir: Path) -> Path:
    path = data_dir / "courses.yaml"
    path.write_text(DISTINCT, encoding="utf-8", newline="\n")
    return path


def _new_professors(config) -> dict[str, list[str]]:
    return {
        p.name: p.course_ids
        for p in Store.from_config(config).load_all().professors
        if p.id not in ("jansen", "whitfield")
    }


def test_every_lecturer_gets_its_own_choice(client: FormClient, config, distinct: Path) -> None:
    rows = dict((name, pick) for pick, name in CREATE_ROW.findall(_page(client)))
    assert set(rows) == {"Prof. I. Иванов", "Prof. P. Петров", "Dr. A. Smith", "Dr. A. Smith."}
    assert len(set(rows.values())) == 4  # Cyrillic names once all slugged to "lecturer"
    res = client.post(URL, data=[("step", "create"), ("pick", rows["Prof. I. Иванов"])])
    assert res.status_code == 303
    assert _new_professors(config) == {"Prof. I. Иванов": ["finance-a"]}  # that one, with its course


def test_tick_all_creates_each_lecturer_once(client: FormClient, config, distinct: Path) -> None:
    html = client.post(URL, data={"select": "all"}).text
    res = client.post(URL, data=[*form_fields(html, FORM), ("step", "create")])
    assert res.status_code == 303 and res.headers["location"] == "/recommenders?created=4&linked=0"
    assert _new_professors(config) == {
        "Prof. I. Иванов": ["finance-a"],
        "Prof. P. Петров": ["finance-b"],
        "Dr. A. Smith": ["stats-a"],
        "Dr. A. Smith.": ["stats-b"],
    }


def test_the_same_choice_twice_is_refused(client: FormClient, data_dir: Path, distinct: Path) -> None:
    pick = CREATE_ROW.findall(_page(client))[0][0]
    before = tree_digest(data_dir)
    res = client.post(URL, data=[("step", "create"), ("pick", pick), ("pick", pick)])
    assert res.status_code == 409 and "not valid" in res.text
    assert tree_digest(data_dir) == before
