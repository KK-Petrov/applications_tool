"""Merge duplicate courses (follow-up before Phase 3; DECISIONS #95; docs/01 §10, docs/03 §4.8).

Checked, fast and through the routes:
- grouping: same code when both copies have one (case, spaces and punctuation ignored), else the same name
  (case and punctuation ignored); institutions equal or one empty; deleted courses are left out;
- the preview (GET /courses/merge) writes nothing; a real conflict has nothing pre-selected and Save waits
  for a pick; a case-only conflict is pre-set to the form in that course's sources/ text;
- Save keeps one course (the chosen one), fills each field from the copy that has it, records owner-picked
  fields as edited, repoints every professor's course_ids (no duplicates), and sends the other copies to
  Deleted courses with ``merged_into`` (their ids are never reused); an unticked copy stays its own course;
- at import, a row matching a stored course is offered "Merge into <course>" (fills only empty fields;
  conflicts shown; the stored value kept) instead of "Already imported"; Accept all leaves it out.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from hub.courses import importer, merging
from hub.models.course import TranscriptCourse
from hub.store import Store

from helpers import FormClient, data_snapshot, form_fields, write_recording

COURSES = """\
courses:
  - id: corporate-finance-feb12003
    name: Corporate Finance
    code: FEB12003
    institution: Example School of Economics
    grade: PASS
    credits: 6.0
    term: 2022-2023 block 2
    lecturers:
      - Dr. L. Ødegaard
    description: ''
    source_document: transcript-a.txt
    confirmed: true
  - id: corporate-finance-feb12003-2
    name: Corporate finance
    code: feb 12003
    institution: ''
    grade: Pass
    credits: 6.0
    term: 2023-2024 block 2
    lecturers: []
    description: Capital structure and valuation.
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
"""
CF1, CF2, MKT = "corporate-finance-feb12003", "corporate-finance-feb12003-2", "marketing-feb12002"


@pytest.fixture
def stored(data_dir: Path) -> Path:
    (data_dir / "courses.yaml").write_text(COURSES, encoding="utf-8", newline="\n")
    (data_dir / "sources").mkdir(exist_ok=True)
    (data_dir / "sources" / "transcript-a.txt").write_text(
        "# Original file: a.pdf\nFEB12003\tCorporate Finance\tPass\t6\t2022-2023 block 2\n", encoding="utf-8"
    )
    (data_dir / "sources" / "transcript-b.txt").write_text(
        "# Original file: b.pdf\nfeb 12003 Corporate finance Pass 6\n", encoding="utf-8"
    )
    return data_dir / "courses.yaml"


def _courses(data_dir: Path) -> dict[str, dict]:
    data = YAML(typ="safe").load((data_dir / "courses.yaml").read_text(encoding="utf-8"))
    return {c["id"]: c for c in data["courses"]}


def _course(
    cid: str, name: str, code: str | None = None, institution: str | None = None, **kw
) -> TranscriptCourse:  # noqa: ANN003
    return TranscriptCourse(id=cid, name=name, code=code, institution=institution, **kw)


# ---- grouping -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        (_course("a-1", "Corporate Finance", "FEB12003"), _course("b-1", "Other name", "feb 12003"), True),
        (_course("a-1", "Corporate Finance"), _course("b-1", "corporate finance."), True),
        (_course("a-1", "Corporate Finance", "FEB12003"), _course("b-1", "Corporate-Finance"), True),
        (
            _course("a-1", "Corporate Finance", "FEB12003"),
            _course("b-1", "Corporate Finance", "FEB12004"),
            False,
        ),
        (
            _course("a-1", "Investments", institution="A"),
            _course("b-1", "Investments", institution="B"),
            False,
        ),
        (_course("a-1", "Investments", institution="A"), _course("b-1", "Investments"), True),
        (_course("a-1", "Investments"), _course("b-1", "Investments II"), False),
    ],
)
def test_two_courses_are_the_same_course(a: TranscriptCourse, b: TranscriptCourse, same: bool) -> None:
    assert merging.same_course(a, b) is same


def test_groups_leave_deleted_courses_out() -> None:
    courses = [
        _course("a-1", "Investments"),
        _course("b-1", "investments"),
        _course("c-1", "Investments", deleted=True),
        _course("d-1", "Marketing"),
    ]
    assert [[c.id for c in g] for g in merging.groups(courses)] == [["a-1", "b-1"]]


# ---- the merge page -----------------------------------------------------------------------------------


def test_the_list_links_to_merge_duplicates(client: FormClient, stored: Path) -> None:
    assert 'href="/courses/merge"' in client.get("/courses").text


def test_the_preview_writes_nothing_and_preselects_only_case_only_conflicts(
    client: FormClient, data_dir: Path, stored: Path
) -> None:
    before = data_snapshot(data_dir)
    html = client.get("/courses/merge").text
    assert {k: v for k, v in data_snapshot(data_dir).items() if not k.startswith("logs/")} == {
        k: v for k, v in before.items() if not k.startswith("logs/")
    }
    assert (
        "Corporate Finance" in html
        and "Marketing" not in html.split("<main", 1)[1].split("Merge duplicates")[-1]
    )
    fields = dict(form_fields(html, "merge-0"))
    assert fields["grade"] == CF2  # "PASS" vs "Pass": "Pass" stands in the sources/ text (CF2's form)
    assert "term" not in fields  # a real conflict: nothing pre-selected
    assert "name" in fields  # "Corporate Finance" vs "Corporate finance": case only, pre-set
    assert fields["keep"] == CF1  # no professor links either: the earliest


def test_save_waits_for_a_pick_on_every_real_conflict(client: FormClient, stored: Path) -> None:
    before = stored.read_bytes()
    fields = form_fields(client.get("/courses/merge").text, "merge-0")
    res = client.post("/courses/merge", data=fields)
    assert res.status_code == 409 and "Pick a value for Code" in res.text  # "FEB12003" vs "feb 12003"
    assert stored.read_bytes() == before


def _merge(client: FormClient, **picks: str) -> object:
    fields = [(k, v) for k, v in form_fields(client.get("/courses/merge").text, "merge-0") if k not in picks]
    return client.post("/courses/merge", data=[*fields, *picks.items()], follow_redirects=True)


def test_save_merges_repoints_professors_and_retires_the_other_copy(
    client: FormClient, config, data_dir: Path, stored: Path
) -> None:
    store = Store.from_config(config)
    for pid, ids in (("jansen", [CF2, MKT]), ("whitfield", [CF1, CF2])):
        prof = next(p for p in store.load_all().professors if p.id == pid)
        store.save_professor(prof.model_copy(update={"course_ids": ids}))
    res = _merge(client, term=CF2, code=CF1, keep=CF1)
    assert res.status_code == 200 and "Merged" in res.text
    courses = _courses(data_dir)
    kept = courses[CF1]
    assert kept["term"] == "2023-2024 block 2"  # picked
    assert kept["description"] == "Capital structure and valuation."  # only CF2 had one
    assert kept["lecturers"] == ["Dr. L. Ødegaard"] and kept["institution"] == "Example School of Economics"
    assert kept["grade"] == "Pass"
    assert set(kept["edited"]) >= {"term", "grade", "name", "code"}
    assert courses[CF2]["deleted"] is True and courses[CF2]["merged_into"] == CF1
    profs = {p.id: p for p in store.load_all().professors}
    assert profs["jansen"].course_ids == [CF1, MKT]
    assert profs["whitfield"].course_ids == [CF1]  # no duplicate
    assert "merged into Corporate Finance" in client.get("/courses/deleted").text


def test_the_kept_copy_can_be_chosen(client: FormClient, data_dir: Path, stored: Path) -> None:
    _merge(client, term=CF1, code=CF1, keep=CF2)
    courses = _courses(data_dir)
    assert courses[CF1]["deleted"] is True and courses[CF1]["merged_into"] == CF2
    assert courses[CF2].get("deleted", False) is False


def test_an_unticked_copy_is_not_merged(client: FormClient, data_dir: Path, stored: Path) -> None:
    fields = [
        (k, v)
        for k, v in form_fields(client.get("/courses/merge").text, "merge-0")
        if not (k == "include" and v == CF2)
    ]
    res = client.post("/courses/merge", data=fields)
    assert res.status_code == 409 and "Tick at least two copies" in res.text


def test_merged_away_ids_are_never_reused(client: FormClient, config, data_dir: Path, stored: Path) -> None:
    _merge(client, term=CF1, code=CF1, keep=CF1)
    taken = Store.from_config(config).load_all().all_course_ids
    assert CF2 in taken
    assert importer.course_id({"name": "Corporate Finance", "code": "FEB12003"}, set(taken)) not in {CF1, CF2}


def test_no_duplicates_says_so(client: FormClient, data_dir: Path) -> None:
    assert "No duplicate courses" in client.get("/courses/merge").text


# ---- at import ----------------------------------------------------------------------------------------


@pytest.fixture
def one_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    row = {
        "name": "Marketing",
        "code": "FEB12002",
        "institution": "Example School of Economics",
        "grade": "6,5",
        "credits": 4.0,
        "term": "2022-2023 block 1",
        "lecturers": ["drs. P. Jansen"],
        "description": "Consumer behaviour and pricing.",
    }
    output = {"courses": [row], "warnings": []}
    monkeypatch.setenv("FAKE_CLAUDE_RECORDING", str(write_recording(tmp_path, output, "courses.json")))
    return row


PASTE = (
    "Courses at Example School of Economics\n"
    "FEB12002\tMarketing\t6,5\t4\t2022-2023 block 1\tdrs. P. Jansen\n"
    "Description: Consumer behaviour and pricing.\n"
)


def _preview(client: FormClient) -> str:
    review = client.post("/courses/import", data={"pasted": PASTE})
    assert review.status_code == 200, review.text[-400:]
    return client.post("/courses/import/send", data=form_fields(review.text, "courses-send")).text


def test_a_matching_import_row_is_offered_merge_not_already_imported(
    client: FormClient, stored: Path, one_row: dict
) -> None:
    html = _preview(client)
    assert "Merge into Marketing" in html and importer.ALREADY_IMPORTED not in html
    assert re.search(r'name="accept" value="merge-0"', html)


def test_accept_all_leaves_merge_rows_out(client: FormClient, stored: Path, one_row: dict) -> None:
    before = stored.read_bytes()
    fields = form_fields(_preview(client), "courses-preview")
    res = client.post("/courses/import/save", data=[*fields, ("accept", "all")])
    assert res.status_code == 409
    assert stored.read_bytes() == before


def test_merge_into_fills_only_empty_fields(
    client: FormClient, data_dir: Path, stored: Path, one_row: dict
) -> None:
    # a conflict on the stored side: the stored term differs from the document's; the stored one is kept
    stored.write_text(COURSES.replace("term: 2022-2023 block 1", "term: Block 1"), encoding="utf-8")
    fields = form_fields(_preview(client), "courses-preview")
    res = client.post("/courses/import/save", data=[*fields, ("accept", "merge-0")])
    assert res.status_code == 200, res.text[-600:]
    mkt = _courses(data_dir)[MKT]
    assert mkt["description"] == "Consumer behaviour and pricing."  # was empty: filled
    assert mkt["term"] == "Block 1" and mkt["grade"] == "6,5"  # stored values kept
    assert "edited" not in mkt  # filled from a transcript: not owner-edited
    assert len(_courses(data_dir)) == 3  # no new course
    assert "Merged into Marketing" in res.text


def test_merge_conflicts_are_shown(client: FormClient, stored: Path, tmp_path: Path, monkeypatch) -> None:
    row = {
        "name": "Marketing",
        "code": "FEB12002",
        "institution": "Example School of Economics",
        "grade": "6,5",
        "credits": 4.0,
        "term": "2022-2023 block 1",
        "lecturers": ["drs. P. Jansen", "Dr. J. Smit"],
        "description": "",
    }
    monkeypatch.setenv(
        "FAKE_CLAUDE_RECORDING",
        str(write_recording(tmp_path, {"courses": [row], "warnings": []}, "courses.json")),
    )
    paste = (
        "Courses at Example School of Economics\n"
        "FEB12002\tMarketing\t6,5\t4\t2022-2023 block 1\tdrs. P. Jansen; Dr. J. Smit\n"
    )
    review = client.post("/courses/import", data={"pasted": paste})
    html = client.post("/courses/import/send", data=form_fields(review.text, "courses-send")).text
    assert "Lecturers differ" in html and "the stored value is kept" in html


# ---- select on the list, Merge all (user request, #95) -----------------------------------------------

CLEAN_PAIR = """\
  - id: statistics-ii-feb13010
    name: Statistics II
    code: FEB13010
    institution: Example School of Economics
    grade: "8,0"
    credits: 4.0
    term: 2023-2024 block 1
    lecturers:
      - Dr. J. de Vries
    description: ''
    source_document: transcript-a.txt
    confirmed: true
  - id: statistics-ii-feb13010-2
    name: STATISTICS II
    code: FEB13010
    institution: ''
    grade: "8,0"
    credits: 4.0
    term: 2023-2024 block 1
    lecturers: []
    description: Regression.
    source_document: transcript-b.txt
    confirmed: true
"""
ST1, ST2 = "statistics-ii-feb13010", "statistics-ii-feb13010-2"


def test_the_list_offers_selection_and_merge_all(client: FormClient, stored: Path) -> None:
    html = client.get("/courses").text
    assert f'name="select" value="{CF1}"' in html
    assert 'value="delete"' in html and 'value="merge"' in html
    assert 'action="/courses/merge-all"' in html


def test_delete_selected_deletes_every_ticked_course(
    client: FormClient, data_dir: Path, stored: Path
) -> None:
    res = client.post(
        "/courses/selected",
        data=[("select", CF2), ("select", MKT), ("action", "delete")],
        follow_redirects=True,
    )
    assert res.status_code == 200 and "2 courses deleted" in res.text
    courses = _courses(data_dir)
    assert courses[CF2]["deleted"] is True and courses[MKT]["deleted"] is True
    assert courses[CF1].get("deleted", False) is False


def test_delete_selected_is_refused_while_any_is_linked(
    client: FormClient, config, data_dir: Path, stored: Path
) -> None:
    store = Store.from_config(config)
    prof = next(p for p in store.load_all().professors if p.id == "jansen")
    store.save_professor(prof.model_copy(update={"course_ids": [MKT]}))
    before = stored.read_bytes()
    res = client.post("/courses/selected", data=[("select", CF2), ("select", MKT), ("action", "delete")])
    assert res.status_code == 409 and prof.name in res.text and "Unlink and delete all selected" in res.text
    assert stored.read_bytes() == before
    res = client.post(
        "/courses/selected",
        data=[("select", CF2), ("select", MKT), ("action", "delete"), ("unlink", "1")],
        follow_redirects=True,
    )
    assert res.status_code == 200
    assert _courses(data_dir)[MKT]["deleted"] is True
    assert next(p for p in store.load_all().professors if p.id == "jansen").course_ids == []


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([("action", "delete")], "Select at least one course"),
        ([("select", MKT), ("action", "merge")], "Select at least two courses to merge"),
    ],
)
def test_too_few_selected_is_refused(client: FormClient, stored: Path, data: list, message: str) -> None:
    before = stored.read_bytes()
    res = client.post("/courses/selected", data=data)
    assert res.status_code == 409 and message in res.text
    assert stored.read_bytes() == before


def test_merge_selected_previews_exactly_the_ticked_courses(
    client: FormClient, data_dir: Path, stored: Path
) -> None:
    before = stored.read_bytes()
    html = client.post("/courses/selected", data=[("select", CF1), ("select", MKT), ("action", "merge")]).text
    assert stored.read_bytes() == before  # a preview: nothing written
    fields = form_fields(html, "merge-0")
    assert sorted(v for k, v in fields if k == "ids") == sorted([CF1, MKT])
    picks = {name: CF1 for name in ("name", "code", "grade", "credits", "term", "lecturers")}
    res = client.post("/courses/merge", data=[*[(k, v) for k, v in fields if k not in picks], *picks.items()])
    assert res.status_code in (200, 303), res.text[-400:]
    assert _courses(data_dir)[MKT]["merged_into"] == CF1


def test_merge_all_merges_groups_without_a_real_conflict_only(
    client: FormClient, data_dir: Path, stored: Path
) -> None:
    stored.write_text(COURSES + CLEAN_PAIR, encoding="utf-8")
    res = client.post("/courses/merge-all", data={}, follow_redirects=True)
    assert res.status_code == 200
    assert "Merged 1 group" in res.text and "1 group needs your choice" in res.text
    courses = _courses(data_dir)
    assert courses[ST2]["merged_into"] == ST1 and courses[ST1]["description"] == "Regression."
    assert courses[ST1]["name"] == "Statistics II"  # case-only: the transcript's form (sources/ of copy a)
    assert courses[CF2].get("deleted", False) is False  # real conflicts: left for the merge page
