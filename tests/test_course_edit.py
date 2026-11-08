"""Edit, delete and restore imported courses (follow-up before Phase 3; DECISIONS #94; docs/01 §10, 03 §4.8).

Fast, through the routes. Checked:
- /courses has an Edit link per course; the form edits name, code, grade, credits, term, institution,
  lecturers and description; it writes through the store (shape and comments kept; an unchanged save writes
  nothing); no AI call;
- every changed field is recorded in ``edited`` (sticky); the list, the recommender panel and Add professor's
  suggestions say "edited by you"; changing grade or credits warns that they came from the transcript;
- a value that looks like an identifier is refused (nothing written; rule 12);
- delete is refused while a professor links the course (the message names them) and offered as "unlink
  and delete"; a deleted course stays in the file with ``deleted: true``, is hidden everywhere, can be
  restored from /courses/deleted, and its id is never reused;
- the /rec-email payload uses the edited values; the manual-description rules of #66 still apply when the
  description is empty.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from hub.ai.payloads import rec_email_payload
from hub.courses import importer
from hub.models.course import TranscriptCourse
from hub.store import Store

from helpers import FormClient, data_snapshot, form_fields, set_field

COURSES = """\
# my courses (a comment the app must keep)
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
  - id: marketing-feb12002
    name: Marketing
    code: FEB12002
    institution: Example School of Economics
    grade: "6,5"
    credits: 4.0
    term: 2022-2023 block 1
    lecturers:
      - drs. P. Jansen
    description: Consumer behaviour.
    source_document: transcript-2026-11-07.txt
    confirmed: true
"""
INV = "investments-feb12006"
MKT = "marketing-feb12002"


@pytest.fixture
def courses_file(data_dir: Path) -> Path:
    path = data_dir / "courses.yaml"
    path.write_text(COURSES, encoding="utf-8", newline="\n")
    return path


def _stored(data_dir: Path) -> dict[str, dict]:
    data = YAML(typ="safe").load((data_dir / "courses.yaml").read_text(encoding="utf-8"))
    return {c["id"]: c for c in data["courses"]}


def _link(config, professor_id: str, course_ids: list[str]) -> None:
    store = Store.from_config(config)
    prof = next(p for p in store.load_all().professors if p.id == professor_id)
    store.save_professor(prof.model_copy(update={"course_ids": course_ids}))


def _edit_form(client: FormClient, cid: str) -> list[tuple[str, str]]:
    res = client.get(f"/courses/{cid}/edit")
    assert res.status_code == 200, res.text[-400:]
    return form_fields(res.text, "course-edit")


# ---- the form and the list -----------------------------------------------------------------------------


def test_the_list_has_an_edit_link_per_course(client: FormClient, courses_file: Path) -> None:
    html = client.get("/courses").text
    assert f'href="/courses/{INV}/edit"' in html and f'href="/courses/{MKT}/edit"' in html
    assert 'href="/courses/deleted"' in html


def test_the_edit_form_has_every_field_and_the_transcript_warning(
    client: FormClient, courses_file: Path
) -> None:
    html = client.get(f"/courses/{INV}/edit").text
    for name in ("name", "code", "grade", "credits", "term", "institution", "lecturers", "description"):
        assert f'name="{name}"' in html, name
    assert "came from your transcript" in html


def test_an_unknown_or_deleted_course_is_not_found(client: FormClient, courses_file: Path) -> None:
    assert client.get("/courses/no-such-course/edit").status_code == 404
    client.post(f"/courses/{INV}/delete", data={})
    assert client.get(f"/courses/{INV}/edit").status_code == 404


# ---- saving --------------------------------------------------------------------------------------------


def test_a_save_writes_the_changes_and_records_them_as_edited(
    client: FormClient, data_dir: Path, courses_file: Path
) -> None:
    fields = _edit_form(client, INV)
    fields = set_field(fields, "description", "Portfolio choice and asset pricing.")
    fields = set_field(fields, "lecturers", "Prof. dr. ir. B. ter Horst\nDr. L. Ødegaard")
    res = client.post(f"/courses/{INV}/edit", data=fields)
    assert res.status_code in (200, 303), res.text[-400:]
    stored = _stored(data_dir)[INV]
    assert stored["description"] == "Portfolio choice and asset pricing."
    assert stored["lecturers"] == ["Prof. dr. ir. B. ter Horst", "Dr. L. Ødegaard"]
    assert stored["edited"] == ["lecturers", "description"]
    assert stored["grade"] == "9,0" and stored["source_document"] == "transcript-2026-11-07.txt"
    assert "edited" not in _stored(data_dir)[MKT]


def test_the_file_keeps_its_shape_and_comment(client: FormClient, data_dir: Path, courses_file: Path) -> None:
    fields = set_field(_edit_form(client, MKT), "term", "2022-2023 block 2")
    client.post(f"/courses/{MKT}/edit", data=fields)
    text = courses_file.read_text(encoding="utf-8")
    assert text.startswith("# my courses (a comment the app must keep)\n")
    import difflib

    diff = [
        line
        for line in difflib.unified_diff(COURSES.splitlines(), text.splitlines(), lineterm="", n=0)
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ]
    # only Marketing's term changes and its `edited` list is added; Investments is untouched
    assert diff == [
        "-    term: 2022-2023 block 1",
        "+    term: 2022-2023 block 2",
        "+    edited:",
        "+      - term",
    ], diff


def test_an_unchanged_save_writes_nothing(client: FormClient, data_dir: Path, courses_file: Path) -> None:
    before = data_snapshot(data_dir)
    res = client.post(f"/courses/{INV}/edit", data=_edit_form(client, INV))
    assert res.status_code in (200, 303)
    after = data_snapshot(data_dir)
    assert {k: v for k, v in after.items() if not k.startswith("logs/")} == {
        k: v for k, v in before.items() if not k.startswith("logs/")
    }


def test_edited_is_sticky(client: FormClient, data_dir: Path, courses_file: Path) -> None:
    client.post(f"/courses/{INV}/edit", data=set_field(_edit_form(client, INV), "term", "Block 3"))
    client.post(f"/courses/{INV}/edit", data=set_field(_edit_form(client, INV), "term", "2022-2023 block 3"))
    stored = _stored(data_dir)[INV]
    assert stored["term"] == "2022-2023 block 3" and stored["edited"] == ["term"]


@pytest.mark.parametrize(("field", "value"), [("grade", "9,5"), ("credits", "7")])
def test_changing_grade_or_credits_warns_that_they_came_from_the_transcript(
    client: FormClient, data_dir: Path, courses_file: Path, field: str, value: str
) -> None:
    res = client.post(
        f"/courses/{INV}/edit", data=set_field(_edit_form(client, INV), field, value), follow_redirects=True
    )
    assert "no longer as your transcript gives" in res.text
    assert field in _stored(data_dir)[INV]["edited"]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("name", "  ", "Course name is required"),
        ("credits", "six", "Credits must be a number"),
        ("credits", "-1", "Credits must be a number"),
        ("description", "x" * 20_001, "Description is too long"),
        ("description", "Student number: 512345", "looks like a student number"),
    ],
)
def test_an_invalid_value_is_refused_and_nothing_is_written(
    client: FormClient, data_dir: Path, courses_file: Path, field: str, value: str, message: str
) -> None:
    before = courses_file.read_bytes()
    res = client.post(f"/courses/{INV}/edit", data=set_field(_edit_form(client, INV), field, value))
    assert res.status_code == 409 and message in res.text, res.text[-600:]
    assert courses_file.read_bytes() == before


def test_editing_makes_no_ai_call(
    client: FormClient, courses_file: Path, tmp_path: Path, monkeypatch
) -> None:
    calls = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(calls))
    client.post(f"/courses/{INV}/edit", data=set_field(_edit_form(client, INV), "term", "Block 3"))
    assert not calls.exists()


# ---- "edited by you" -----------------------------------------------------------------------------------


def test_the_list_panel_and_suggestions_say_edited_by_you(
    client: FormClient, config, courses_file: Path
) -> None:
    client.post(f"/courses/{INV}/edit", data=set_field(_edit_form(client, INV), "description", "Mine."))
    _link(config, "jansen", [INV])
    assert "edited by you" in client.get("/courses").text
    assert "edited by you" in client.get("/recommenders?selected=jansen").text
    assert (
        "edited by you"
        in client.get("/recommenders/course-suggestions", params={"name": "B. ter Horst"}).text
    )


# ---- the email payload ---------------------------------------------------------------------------------


def _payload_courses(config, professor_update: dict) -> list[dict]:
    snapshot = Store.from_config(config).load_all()
    prof = next(p for p in snapshot.professors if p.id == "jansen").model_copy(update=professor_update)
    payload = rec_email_payload(
        prof,
        snapshot.programmes[:1],
        snapshot.biography,
        snapshot.settings.owner_name,
        datetime(2026, 10, 30, 16, tzinfo=UTC),
        config,
        snapshot.courses,
    )
    return payload["professor"]["courses"]


def test_the_email_payload_uses_the_edited_values(client: FormClient, config, courses_file: Path) -> None:
    fields = set_field(_edit_form(client, MKT), "description", "Pricing and promotion, as I remember it.")
    client.post(f"/courses/{MKT}/edit", data=set_field(fields, "name", "Marketing Management"))
    # the fixture professor also has a manual course, sent as its own entry (#66 (c)): pick the linked one
    course = next(c for c in _payload_courses(config, {"course_ids": [MKT]}) if c["code"] == "FEB12002")
    assert course["name"] == "Marketing Management"
    assert course["description"] == "Pricing and promotion, as I remember it."


def test_an_emptied_description_falls_back_to_the_manual_rules(
    client: FormClient, config, courses_file: Path
) -> None:
    """#66 (b): the only linked course without a description gets the professor's manual description."""
    client.post(f"/courses/{MKT}/edit", data=set_field(_edit_form(client, MKT), "description", ""))
    courses = _payload_courses(
        config, {"course_ids": [MKT], "course": "Something else", "course_description": "Pasted syllabus."}
    )
    assert [c["description"] for c in courses] == ["Pasted syllabus."]


# ---- delete and restore --------------------------------------------------------------------------------


def test_delete_hides_the_course_keeps_it_in_the_file_and_never_reuses_its_id(
    client: FormClient, config, data_dir: Path, courses_file: Path
) -> None:
    res = client.post(f"/courses/{INV}/delete", data={}, follow_redirects=True)
    assert res.status_code == 200
    assert _stored(data_dir)[INV]["deleted"] is True
    assert f'href="/courses/{INV}/edit"' not in client.get("/courses").text
    assert (
        "Investments"
        not in client.get("/recommenders/course-suggestions", params={"name": "B. ter Horst"}).text
    )
    taken = Store.from_config(config).load_all().all_course_ids
    row = {"name": "Investments", "code": "FEB12006"}
    assert importer.course_id(row, set(taken)) != INV


def test_delete_is_refused_while_a_professor_links_the_course(
    client: FormClient, config, data_dir: Path, courses_file: Path
) -> None:
    _link(config, "jansen", [MKT])
    before = data_snapshot(data_dir)
    res = client.post(f"/courses/{MKT}/delete", data={})
    assert res.status_code == 409
    name = next(p for p in Store.from_config(config).load_all().professors if p.id == "jansen").name
    assert name in res.text and "Unlink from these professors and delete" in res.text
    after = data_snapshot(data_dir)
    assert {k: v for k, v in after.items() if not k.startswith("logs/")} == {
        k: v for k, v in before.items() if not k.startswith("logs/")
    }


def test_unlink_and_delete(client: FormClient, config, data_dir: Path, courses_file: Path) -> None:
    _link(config, "jansen", [MKT, INV])
    res = client.post(f"/courses/{MKT}/delete", data={"unlink": "1"}, follow_redirects=True)
    assert res.status_code == 200
    prof = next(p for p in Store.from_config(config).load_all().professors if p.id == "jansen")
    assert prof.course_ids == [INV]
    assert _stored(data_dir)[MKT]["deleted"] is True


def test_the_deleted_page_restores_the_ticked_courses(
    client: FormClient, data_dir: Path, courses_file: Path
) -> None:
    client.post(f"/courses/{INV}/delete", data={})
    client.post(f"/courses/{MKT}/delete", data={})
    html = client.get("/courses/deleted").text
    assert f'value="{INV}"' in html and f'value="{MKT}"' in html
    res = client.post("/courses/restore", data=[("restore", INV)], follow_redirects=True)
    assert res.status_code == 200
    stored = _stored(data_dir)
    assert stored[INV].get("deleted", False) is False and stored[MKT]["deleted"] is True
    assert f'href="/courses/{INV}/edit"' in client.get("/courses").text


def test_restore_is_refused_when_the_course_is_again_among_your_courses(
    client: FormClient, data_dir: Path, courses_file: Path, config
) -> None:
    client.post(f"/courses/{INV}/delete", data={})
    taken = set(_stored(data_dir))
    again = {
        "name": "Investments",
        "code": "FEB12006",
        "institution": "Example School of Economics",
        "term": "2022-2023 block 3",
        "grade": "9,0",
        "credits": 6.0,
        "lecturers": [],
        "description": "",
    }
    Store.from_config(config).add_courses(
        [importer.to_course(again, importer.course_id(again, taken), "x.txt")]
    )
    before = courses_file.read_bytes()
    res = client.post("/courses/restore", data=[("restore", INV)])
    assert res.status_code == 409 and "already among your courses" in res.text
    assert courses_file.read_bytes() == before


def test_a_deleted_course_is_not_in_the_payload_or_the_snapshot(
    client: FormClient, config, courses_file: Path
) -> None:
    client.post(f"/courses/{INV}/delete", data={})
    snapshot = Store.from_config(config).load_all()
    assert [c.id for c in snapshot.courses] == [MKT]
    assert INV in snapshot.all_course_ids


def test_a_deleted_course_is_never_offered_for_linking(
    client: FormClient, config, courses_file: Path
) -> None:
    client.post(f"/courses/{INV}/delete", data={})
    assert INV not in client.get("/recommenders/new").text


def test_the_model_rejects_an_unknown_edited_field() -> None:
    with pytest.raises(ValueError):
        TranscriptCourse(id="x-1", name="X", edited=["secret"])
