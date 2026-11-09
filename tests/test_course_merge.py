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
from hub.store import DataError, Store

from helpers import FormClient, data_snapshot, form_fields, tree_digest, write_recording

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


def _g(html: str, g: int = 0) -> list[tuple[str, str]]:
    """Group ``g``'s fields in the merge form, without their ``g<n>-`` prefix, plus the form's digest (the
    whole page is one form since the batch merge, DECISIONS #101)."""
    out = []
    for k, v in form_fields(html, "merge-form"):
        if k == "digest":
            out.append((k, v))
        elif k.startswith(f"g{g}-"):
            out.append((k[len(f"g{g}-") :], v))
    return out


def _post(client: FormClient, fields: list[tuple[str, str]], g: int = 0, **kw) -> object:  # noqa: ANN003
    """Post group ``g``'s fields (un-prefixed, as ``_g`` returns them) with that group's own Merge button."""
    data = [(k if k == "digest" else f"g{g}-{k}", v) for k, v in fields]
    return client.post("/courses/merge", data=[*data, ("merge", str(g))], **kw)


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
    fields = dict(_g(html))
    assert fields["grade"] == CF2  # "PASS" vs "Pass": "Pass" stands in the sources/ text (CF2's form)
    assert "term" not in fields  # a real conflict: nothing pre-selected
    assert "name" in fields  # "Corporate Finance" vs "Corporate finance": case only, pre-set
    assert fields["keep"] == CF1  # no professor links either: the earliest


def test_save_waits_for_a_pick_on_every_real_conflict(client: FormClient, stored: Path) -> None:
    before = stored.read_bytes()
    fields = _g(client.get("/courses/merge").text)
    res = _post(client, fields)
    assert res.status_code == 409 and "Pick a value for Code" in res.text  # "FEB12003" vs "feb 12003"
    assert stored.read_bytes() == before


def _merge(client: FormClient, **picks: str) -> object:
    fields = [(k, v) for k, v in _g(client.get("/courses/merge").text) if k not in picks]
    return _post(client, [*fields, *picks.items()], follow_redirects=True)


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
    fields = [(k, v) for k, v in _g(client.get("/courses/merge").text) if not (k == "include" and v == CF2)]
    res = _post(client, fields)
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
    active = [c for c in _courses(data_dir).values() if not c.get("deleted")]
    assert len(active) == 3  # no new active course (the row is kept as a retired copy, DECISIONS #96)
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


# ---- a merged row in that document's "Create professors" step (DECISIONS #96) -------------------------


def _merge_row(client: FormClient) -> tuple[object, str]:
    """Merge the one import row into the stored Marketing course; the response and the document's source."""
    fields = form_fields(_preview(client), "courses-preview")
    res = client.post("/courses/import/save", data=[*fields, ("accept", "merge-0")])
    assert res.status_code == 200, res.text[-600:]
    source = dict(form_fields(res.text, "professors-start"))["source"]
    assert source and source != "transcript-a.txt"
    return res, source


def _retired(data_dir: Path, source: str) -> list[dict]:
    return [c for c in _courses(data_dir).values() if c.get("source_document") == source]


def test_merge_into_keeps_the_row_as_a_retired_copy_of_this_document(
    client: FormClient, data_dir: Path, stored: Path, one_row: dict
) -> None:
    _, source = _merge_row(client)
    copies = _retired(data_dir, source)
    assert len(copies) == 1
    copy = copies[0]
    assert copy["deleted"] is True and copy["merged_into"] == MKT and copy["confirmed"] is True
    assert copy["lecturers"] == ["drs. P. Jansen"] and copy["name"] == "Marketing"
    assert copy["id"] not in {CF1, CF2, MKT}
    assert _courses(data_dir)[MKT].get("deleted", False) is False
    listed = client.get("/courses").text
    assert listed.count(">Marketing<") <= 1 and copy["id"] not in listed  # the copy is not an active course


def test_create_professors_lists_a_merged_rows_lecturers_linked_to_the_kept_course(
    client: FormClient, config, data_dir: Path, stored: Path, one_row: dict
) -> None:
    res, source = _merge_row(client)
    page = client.post("/courses/import/professors", data=form_fields(res.text, "professors-start"))
    assert page.status_code == 200, page.text[-600:]
    assert "drs. P. Jansen" in page.text and "Marketing" in page.text
    assert "No accepted course from this document" not in page.text
    fields = form_fields(page.text, "professors-preview")
    pick = next(v for v in re.findall(r'name="pick" value="([^"]+)"', page.text) if v.startswith("create:"))
    done = client.post("/courses/import/professors", data=[*fields, ("pick", pick)])
    assert done.status_code == 303, done.text[-600:]
    created = [p for p in Store.from_config(config).load_all().professors if p.name == "drs. P. Jansen"]
    assert len(created) == 1 and created[0].course_ids == [MKT]  # the kept course, never the retired copy


def test_the_rows_own_lecturers_are_listed_when_the_stored_course_has_others(
    client: FormClient, data_dir: Path, stored: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = {
        "name": "Marketing", "code": "FEB12002", "institution": "Example School of Economics",
        "grade": "6,5", "credits": 4.0, "term": "2022-2023 block 1", "lecturers": ["Dr. J. Smit"],
        "description": "",
    }  # fmt: skip
    monkeypatch.setenv(
        "FAKE_CLAUDE_RECORDING",
        str(write_recording(tmp_path, {"courses": [row], "warnings": []}, "courses.json")),
    )
    paste = (
        "Courses at Example School of Economics\n"
        "FEB12002\tMarketing\t6,5\t4\t2022-2023 block 1\tDr. J. Smit\n"
    )
    review = client.post("/courses/import", data={"pasted": paste})
    html = client.post("/courses/import/send", data=form_fields(review.text, "courses-send")).text
    fields = form_fields(html, "courses-preview")
    res = client.post("/courses/import/save", data=[*fields, ("accept", "merge-0")])
    assert res.status_code == 200, res.text[-600:]
    assert _courses(data_dir)[MKT]["lecturers"] == ["drs. P. Jansen"]  # the stored value is kept
    page = client.post("/courses/import/professors", data=form_fields(res.text, "professors-start")).text
    assert "Dr. J. Smit" in page  # as this document writes them
    assert "drs. P. Jansen" not in page  # the stored course's lecturer comes from another document


def test_merging_the_same_row_again_adds_no_second_copy(
    client: FormClient, data_dir: Path, stored: Path, one_row: dict
) -> None:
    _, source = _merge_row(client)
    fields = form_fields(_preview(client), "courses-preview")  # the same document imported again
    res = client.post("/courses/import/save", data=[*fields, ("accept", "merge-0")])
    assert res.status_code == 200, res.text[-600:]
    assert len(_retired(data_dir, source)) == 1


def test_the_step_follows_a_later_merge_to_the_course_that_remains(
    client: FormClient, data_dir: Path, stored: Path, one_row: dict
) -> None:
    res, source = _merge_row(client)
    # Marketing is merged away later into Corporate Finance (owner's choice on the merge page)
    html = client.post("/courses/selected", data=[("select", CF1), ("select", MKT), ("action", "merge")]).text
    fields = _g(html)
    picks = {n: CF1 for n in ("name", "code", "grade", "credits", "term", "lecturers", "description")}
    picks["keep"] = CF1
    saved = _post(client, [*[(k, v) for k, v in fields if k not in picks], *picks.items()])
    assert saved.status_code in (200, 303), saved.text[-400:]
    page = client.post("/courses/import/professors", data=form_fields(res.text, "professors-start")).text
    assert "drs. P. Jansen" in page and "Corporate Finance" in page
    assert 'value="create:' in page


def test_a_copy_merged_away_on_the_merge_page_still_counts_for_its_document(
    client: FormClient, config, data_dir: Path, stored: Path
) -> None:
    # CF1 (transcript-a, lecturer Ødegaard) merged into CF2 (transcript-b): transcript-a's step keeps Ødegaard
    _merge(client, term=CF1, code=CF1, keep=CF2)
    page = client.post("/courses/import/professors", data={"source": "transcript-a.txt"}).text
    assert "Ødegaard" in page and "drs. P. Jansen" in page  # Marketing is still transcript-a's own course
    fields = form_fields(page, "professors-preview")
    pick = next(v for v in re.findall(r'name="pick" value="([^"]+)"', page) if "degaard" in v)
    client.post("/courses/import/professors", data=[*fields, ("pick", pick)])
    created = [p for p in Store.from_config(config).load_all().professors if "degaard" in p.name]
    assert created and created[0].course_ids == [CF2]


def test_a_deleted_course_stays_out_of_the_step(client: FormClient, data_dir: Path, stored: Path) -> None:
    head = "    source_document: transcript-a.txt\n    confirmed: true\n  - id: corporate-finance-feb12003-2"
    deleted = head.replace("confirmed: true\n", "confirmed: true\n    deleted: true\n", 1)
    stored.write_text(COURSES.replace(head, deleted), encoding="utf-8")  # CF1 deleted, not merged
    page = client.post("/courses/import/professors", data={"source": "transcript-a.txt"}).text
    assert "Ødegaard" not in page and "drs. P. Jansen" in page


def test_restoring_a_retired_import_copy_is_refused(
    client: FormClient, data_dir: Path, stored: Path, one_row: dict
) -> None:
    _, source = _merge_row(client)
    copy = _retired(data_dir, source)[0]
    before = stored.read_bytes()
    res = client.post("/courses/restore", data={"restore": copy["id"]})
    assert res.status_code == 409 and "was merged into Marketing, which is still on your list" in res.text
    assert stored.read_bytes() == before


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
    fields = _g(html)
    assert sorted(v for k, v in fields if k == "ids") == sorted([CF1, MKT])
    picks = {name: CF1 for name in ("name", "code", "grade", "credits", "term", "lecturers")}
    res = _post(client, [*[(k, v) for k, v in fields if k not in picks], *picks.items()])
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


# ---- ui review round 7 (F1–F4) and the restore check (F2) ---------------------------------------------


def test_restoring_a_merged_import_copy_that_differs_is_refused(
    client: FormClient, data_dir: Path, stored: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # r7 F2: an import row with no institution and another grade and term matches Marketing by code only
    row = {
        "name": "Marketing", "code": "FEB12002", "institution": None, "grade": "7,0", "credits": 4.0,
        "term": "2023-2024 block 1", "lecturers": [], "description": "",
    }  # fmt: skip
    monkeypatch.setenv(
        "FAKE_CLAUDE_RECORDING",
        str(write_recording(tmp_path, {"courses": [row], "warnings": []}, "courses.json")),
    )
    review = client.post(
        "/courses/import", data={"pasted": "FEB12002\tMarketing\t7,0\t4\t2023-2024 block 1\n"}
    )
    html = client.post("/courses/import/send", data=form_fields(review.text, "courses-send")).text
    res = client.post(
        "/courses/import/save", data=[*form_fields(html, "courses-preview"), ("accept", "merge-0")]
    )
    assert res.status_code == 200, res.text[-600:]
    copy = next(c for c in _courses(data_dir).values() if c.get("merged_into") == MKT)
    before = stored.read_bytes()
    refused = client.post("/courses/restore", data={"restore": copy["id"]})
    assert refused.status_code == 409
    assert "was merged into Marketing, which is still on your list" in refused.text
    assert stored.read_bytes() == before


def test_a_restored_copy_is_no_longer_merged_into(client: FormClient, data_dir: Path, stored: Path) -> None:
    _merge(client, term=CF1, code=CF1, keep=CF1)  # CF2 merged into CF1
    assert client.post(f"/courses/{CF1}/delete", data={}).status_code in (200, 303)
    res = client.post("/courses/restore", data={"restore": CF2})
    assert res.status_code == 303, res.text[-400:]
    back = _courses(data_dir)[CF2]
    assert back.get("deleted", False) is False and back.get("merged_into") is None


def _selected_form(client: FormClient) -> list[tuple[str, str]]:
    html = client.post("/courses/selected", data=[("select", CF1), ("select", MKT), ("action", "merge")]).text
    return _g(html)


def test_a_refused_merge_selected_comes_back_with_the_same_group_and_choices(
    client: FormClient, stored: Path
) -> None:
    # r7 F1: the posted group, its keep and its picks come back with the message inside that form
    fields = [(k, v) for k, v in _selected_form(client) if k != "keep"]
    before = stored.read_bytes()
    res = _post(client, [*fields, ("keep", MKT), ("name", MKT)])
    assert res.status_code == 409 and stored.read_bytes() == before
    back = _g(res.text)
    assert sorted(v for k, v in back if k == "ids") == sorted([CF1, MKT])
    assert ("keep", MKT) in back and ("name", MKT) in back
    form = res.text[res.text.index('id="merge-0"') :]
    form = form[: form.index("</form>")]
    assert 'id="merge-0-error"' in form and "Pick a value" in form
    assert 'data-focus="merge-0-error"' in res.text


def test_a_refused_merge_keeps_unticked_copies_unticked(client: FormClient, stored: Path) -> None:
    fields = [(k, v) for k, v in _g(client.get("/courses/merge").text)]
    fields = [(k, v) for k, v in fields if not (k == "include" and v == CF2)]
    res = _post(client, fields)
    assert res.status_code == 409 and "Tick at least two copies" in res.text
    back = _g(res.text)
    assert ("include", CF1) in back and ("include", CF2) not in back
    assert sorted(v for k, v in back if k == "ids") == sorted([CF1, CF2])


def test_each_copys_controls_are_described_by_that_copy(client: FormClient, stored: Path) -> None:
    # r7 F3: "Include in the merge" and "Keep this one" are told apart by the copy they belong to
    html = client.get("/courses/merge").text
    for cid, term in ((CF1, "2022-2023 block 2"), (CF2, "2023-2024 block 2")):
        for name in ("include", "keep"):
            tag = re.search(rf'<input[^>]*name="g0-{name}" value="{cid}"[^>]*>', html).group(0)
            described = re.search(r'aria-describedby="([^"]+)"', tag).group(1)
            text = re.search(rf'id="{described}"[^>]*>(.*?)</', html, re.S).group(1)
            assert term in text


HAND_ADDED = """\
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
  - id: investments
    name: Investments
    grade: "8,5"
    description: Typed by hand.
    source_document: null
    confirmed: false
"""


def test_a_hand_added_copy_is_named_so_never_by_its_id(client: FormClient, stored: Path) -> None:
    # r7 F4
    stored.write_text(COURSES + HAND_ADDED, encoding="utf-8")
    html = client.get("/courses/merge").text
    group = html[html.index("Investments: 2 copies") :]
    assert "(investments)" not in group and "(added by hand)" in group
    assert "added by hand" in re.search(r'id="merge-\d+-copy-1"[^>]*>(.*?)</', group, re.S).group(1)


# ---- data-integrity review round 11, F2: ticked courses are checked against each other ---------------

PAIR_DELETED = """courses:
  - id: marketing-feb12002
    name: Marketing
    code: FEB12002
    institution: Example School of Economics
    grade: "6,5"
    credits: 4.0
    term: 2022-2023 block 1
    lecturers: []
    description: ''
    source_document: transcript-a.txt
    confirmed: true
    deleted: true
  - id: marketing
    name: Marketing
    code: FEB12002
    grade: "7,0"
    credits: 4.0
    term: 2023-2024 block 1
    lecturers: []
    description: ''
    source_document: master-profile.txt
    confirmed: true
    deleted: true
    merged_into: marketing-feb12002
"""


def test_restoring_a_course_with_its_merged_copy_is_refused(client: FormClient, stored: Path) -> None:
    stored.write_text(PAIR_DELETED, encoding="utf-8")
    before = stored.read_bytes()
    res = client.post("/courses/restore", data=[("restore", "marketing-feb12002"), ("restore", "marketing")])
    assert res.status_code == 409 and "Nothing was restored" in res.text
    assert "Marketing" in res.text and "the same course" in res.text
    assert stored.read_bytes() == before


def test_restoring_either_one_alone_still_works(client: FormClient, data_dir: Path, stored: Path) -> None:
    stored.write_text(PAIR_DELETED, encoding="utf-8")
    assert client.post("/courses/restore", data={"restore": "marketing-feb12002"}).status_code == 303
    assert _courses(data_dir)["marketing-feb12002"].get("deleted", False) is False


# ---- batch merge: "Merge all decided groups" (user request, DECISIONS #101) ---------------------------


@pytest.fixture
def two_groups(stored: Path) -> Path:
    """Corporate Finance (real conflicts on code and term: undecided until picked) and Statistics II (only a
    case-only name difference, pre-set: decided as shown)."""
    stored.write_text(COURSES + CLEAN_PAIR, encoding="utf-8")
    return stored


def _all(html: str) -> list[tuple[str, str]]:
    return form_fields(html, "merge-form")


def _batch(client: FormClient, fields: list[tuple[str, str]], **kw) -> object:  # noqa: ANN003
    return client.post("/courses/merge", data=[*fields, ("merge", "decided")], **kw)


def test_the_merge_page_is_one_form_with_a_batch_button(client: FormClient, two_groups: Path) -> None:
    html = client.get("/courses/merge").text
    assert html.count("<form") - html.count('id="zone-form"') >= 1
    fields = _all(html)
    assert {k.split("-", 1)[0] for k, _ in fields if k != "digest"} == {"g0", "g1"}
    assert 'name="merge" value="decided"' in html and "Merge all decided groups" in html
    assert html.count('name="merge" value="0"') == 1 and html.count('name="merge" value="1"') == 1


def test_only_decided_groups_merge(client: FormClient, data_dir: Path, two_groups: Path) -> None:
    res = _batch(client, _all(client.get("/courses/merge").text))
    assert res.status_code == 200, res.text[-400:]
    courses = _courses(data_dir)
    assert courses[ST2]["merged_into"] == ST1 and courses[ST1]["description"] == "Regression."
    assert courses[CF2].get("deleted", False) is False and courses[CF1]["term"] == "2022-2023 block 2"
    assert "Merged 1 group; 1 still needs a choice." in res.text
    assert 'data-focus="merge-notice"' in res.text and 'data-announce="Merged 1 group' in res.text


def test_an_undecided_group_comes_back_with_the_posted_choices(
    client: FormClient, data_dir: Path, two_groups: Path
) -> None:
    fields = [(k, v) for k, v in _all(client.get("/courses/merge").text) if k != "g0-keep"]
    fields += [("g0-keep", CF2), ("g0-term", CF2)]  # the term picked, the code not
    res = _batch(client, fields)
    back = _all(res.text)
    assert ("g0-keep", CF2) in back and ("g0-term", CF2) in back  # the owner's choices kept
    assert "Pick a value for Code" in res.text  # what the group still needs, inside it
    assert not any(k.startswith("g1-") for k, _ in back)  # the merged group is gone from the page


def test_a_group_is_all_or_nothing(client: FormClient, data_dir: Path, two_groups: Path) -> None:
    fields = [*_all(client.get("/courses/merge").text), ("g0-term", CF2)]  # code still missing
    _batch(client, fields)
    courses = _courses(data_dir)
    assert courses[CF1]["term"] == "2022-2023 block 2"  # nothing of the undecided group written
    assert courses[CF2].get("deleted", False) is False


def test_every_decided_group_merges_and_the_list_says_how_many(
    client: FormClient, data_dir: Path, two_groups: Path
) -> None:
    fields = [*_all(client.get("/courses/merge").text), ("g0-term", CF2), ("g0-code", CF1)]
    res = _batch(client, fields)
    assert res.status_code == 303 and res.headers["location"] == "/courses?merged_all=2&waiting=0"
    assert "Merged 2 groups of duplicates." in client.get(res.headers["location"]).text
    courses = _courses(data_dir)
    assert courses[CF2]["merged_into"] == CF1 and courses[ST2]["merged_into"] == ST1


def test_nothing_decided_writes_nothing(client: FormClient, data_dir: Path, two_groups: Path) -> None:
    fields = [(k, v) for k, v in _all(client.get("/courses/merge").text) if k != "g1-keep"]
    before = two_groups.read_bytes()
    res = _batch(client, fields)
    assert res.status_code == 409 and "Merged 0 groups; 2 still need a choice." in res.text
    assert two_groups.read_bytes() == before


def test_professors_are_repointed_once(
    client: FormClient, config, data_dir: Path, two_groups: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = Store.from_config(config)
    jansen = next(p for p in store.load_all().professors if p.id == "jansen")
    store.save_professor(jansen.model_copy(update={"course_ids": [CF2, ST2]}))
    calls: list[str] = []
    real = Store.save_professor

    def counting(self, professor):  # noqa: ANN001, ANN202
        calls.append(professor.id)
        return real(self, professor)

    monkeypatch.setattr(Store, "save_professor", counting)
    page = [(k, v) for k, v in _all(client.get("/courses/merge").text) if not k.endswith("-keep")]
    # keep the copies Jansen does not link (by default the linked copy is kept: nothing would move)
    fields = [*page, ("g0-keep", CF1), ("g1-keep", ST1), ("g0-term", CF2), ("g0-code", CF1)]
    assert _batch(client, fields).status_code == 303
    assert calls == ["jansen"]  # one write for both groups
    assert next(p for p in store.load_all().professors if p.id == "jansen").course_ids == [CF1, ST1]


@pytest.mark.parametrize("changed", ["courses.yaml", "professors/jansen.yaml"])
def test_a_stale_page_is_refused_and_writes_nothing(
    client: FormClient, data_dir: Path, two_groups: Path, changed: str
) -> None:
    fields = [*_all(client.get("/courses/merge").text), ("g0-term", CF2), ("g0-code", CF1)]
    path = data_dir / changed
    path.write_text(path.read_text(encoding="utf-8") + "# changed in another tab\n", encoding="utf-8")
    before = data_snapshot(data_dir)
    res = _batch(client, fields)
    assert res.status_code == 409 and "changed since this page was shown" in res.text
    assert "Nothing was merged" in res.text
    assert {k: v for k, v in data_snapshot(data_dir).items() if not k.startswith("logs/")} == {
        k: v for k, v in before.items() if not k.startswith("logs/")
    }
    back = _all(res.text)
    assert ("g0-term", CF2) in back and ("g0-code", CF1) in back  # the choices shown again


def test_a_post_without_the_digest_is_refused(client: FormClient, two_groups: Path) -> None:
    fields = [(k, v) for k, v in _all(client.get("/courses/merge").text) if k != "digest"]
    before = two_groups.read_bytes()
    assert _batch(client, fields).status_code == 409
    assert two_groups.read_bytes() == before


def test_a_groups_own_button_still_merges_only_that_group(
    client: FormClient, data_dir: Path, two_groups: Path
) -> None:
    res = client.post("/courses/merge", data=[*_all(client.get("/courses/merge").text), ("merge", "1")])
    assert res.status_code == 303
    courses = _courses(data_dir)
    assert courses[ST2]["merged_into"] == ST1 and courses[CF2].get("deleted", False) is False


# ---- data-integrity Phase 3 r1 F4, F5, F6 (DECISIONS #105) ----------------------------------------------


def _decided(client: FormClient) -> list[tuple[str, str]]:
    """Both groups decided, keeping the copies the professors below do not link (so both are repointed)."""
    page = [(k, v) for k, v in _all(client.get("/courses/merge").text) if not k.endswith("-keep")]
    return [*page, ("g0-keep", CF1), ("g1-keep", ST1), ("g0-term", CF2), ("g0-code", CF1)]


def _link(config, pid: str, course_ids: list[str]) -> None:
    store = Store.from_config(config)
    prof = next(p for p in store.load_all().professors if p.id == pid)
    store.save_professor(prof.model_copy(update={"course_ids": course_ids}))


BROKEN = "id: broken\nname: Dr. B. Broken\nuniversity: Example\ncourse_ids: [{cid}]\nnot_a_field: 1\n"


@pytest.mark.parametrize("how", ["batch", "own button", "merge all"])
def test_merging_is_refused_while_a_professor_file_cannot_be_read(
    client: FormClient, data_dir: Path, two_groups: Path, how: str
) -> None:
    """F4: an unreadable professor file linking a copy would keep linking it after the merge (docs/01 §10)."""
    fields = _decided(client)
    (data_dir / "professors" / "broken.yaml").write_text(BROKEN.format(cid=ST2), encoding="utf-8")
    before = tree_digest(data_dir)
    if how == "merge all":
        res = client.post("/courses/merge-all")
    else:
        refreshed = [(k, v) for k, v in fields if k != "digest"] + [("digest", _digest_now(client))]
        button = "decided" if how == "batch" else "1"
        res = client.post("/courses/merge", data=[*refreshed, ("merge", button)])
    assert res.status_code == 409 and "professors/broken.yaml" in res.text
    assert "Nothing was merged" in res.text
    assert tree_digest(data_dir) == before


def _digest_now(client: FormClient) -> str:
    return dict(_all(client.get("/courses/merge").text))["digest"]


@pytest.mark.parametrize(
    ("failing", "message"),
    [
        ("save_professor", "1 of 2 professors were repointed, then it stopped: professors/"),
        ("save_courses", "2 of 2 professors were repointed, then it stopped: courses.yaml"),
    ],
)
def test_a_write_failure_part_way_says_what_happened(
    client: FormClient,
    config,
    data_dir: Path,
    two_groups: Path,
    monkeypatch: pytest.MonkeyPatch,
    failing: str,
    message: str,
) -> None:
    """F5: no bare 500; the professors already repointed point at active kept courses, no course is merged."""
    _link(config, "jansen", [CF2])
    _link(config, "whitfield", [ST2])
    fields = _decided(client)
    courses_before = two_groups.read_bytes()
    real = getattr(Store, failing)
    calls: list[str] = []

    def fail(self, model):  # noqa: ANN001, ANN202
        calls.append("x")
        if failing == "save_courses" or len(calls) == 2:
            raise DataError(
                "courses.yaml" if failing == "save_courses" else f"professors/{model.id}.yaml",
                None,
                "could not be written: it is locked",
            )
        return real(self, model)

    monkeypatch.setattr(Store, failing, fail)
    res = _batch(client, fields)
    assert res.status_code == 409 and message in res.text
    assert "No course was merged yet" in res.text
    assert two_groups.read_bytes() == courses_before  # no copy deleted, no merged values


def _write_during_load(monkeypatch: pytest.MonkeyPatch, path: Path, when: str) -> None:
    """Change ``path`` once, just before or just after the next ``Store.load_all`` reads the files (another
    tab or OneDrive writing in between)."""
    real = Store.load_all
    state = {"done": False}

    def load_all(self):  # noqa: ANN001, ANN202
        def change() -> None:
            if not state["done"]:
                state["done"] = True
                path.write_text(path.read_text(encoding="utf-8") + "# changed meanwhile\n", encoding="utf-8")

        if when == "before":
            change()
        result = real(self)
        if when == "after":
            change()
        return result

    monkeypatch.setattr(Store, "load_all", load_all)


def test_a_change_while_the_merge_page_loads_is_caught_on_submit(
    client: FormClient, data_dir: Path, two_groups: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F6: the page must carry the digest of what it shows, taken before loading."""
    _write_during_load(monkeypatch, data_dir / "professors" / "jansen.yaml", "after")
    fields = _decided(client)
    before = two_groups.read_bytes()
    res = _batch(client, fields)
    assert res.status_code == 409 and "changed since this page was shown" in res.text
    assert two_groups.read_bytes() == before
