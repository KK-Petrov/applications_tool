"""Course import and professors from a document (C-2c-03 fast part; docs/02 §9, §10; docs/04 §9; #44).

Fast, with the fake Claude Code answering ``/extract-courses`` with the expected rows of the invented fixture
transcript (tests/evals/courses/). Checked:
- identifiers are stripped in Python, label-anchored (same line; label on one line and the value on the next;
  a table's label row followed by its value row), in English and Dutch, in all three formats; a standalone
  9-digit number only if it passes the BSN 11-check; course codes, grades, credits and look-alikes survive;
  a label whose value can't be found stops the import before anything is sent to the model;
- every identifier is gone from the preview, the form and everything written to data_dir (sources/ included);
- a grade is accepted only if it stands on the same line or table row as its course's name or code; an
  invented or swapped grade is flagged and blocks its row;
- a scanned PDF is refused; nothing (courses or professors) is written before the user confirms;
- each lecturer is listed once with all their courses; a match with an existing professor is offered for
  linking; batch creation never overwrites a file.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from hub.config import load_config
from hub.courses import extract, identifiers, importer, lecturers
from hub.models.course import TranscriptCourse
from hub.models.professor import Professor
from hub.store import Store

from helpers import FormClient, assert_only_usage_line, data_snapshot, form_fields, set_field, write_recording
from paths import REPO_ROOT

EVAL = REPO_ROOT / "tests" / "evals" / "courses"
EXPECTED = json.loads((EVAL / "expected.json").read_text(encoding="utf-8"))
FORMATS = {"txt": "transcript.txt", "docx": "transcript.docx", "pdf": "transcript.pdf"}
CFG = load_config()


def _text(fmt: str) -> str:
    name = FORMATS[fmt]
    if fmt == "txt":
        return extract.text_from_paste((EVAL / name).read_text(encoding="utf-8"))
    return extract.extract_text(name, (EVAL / name).read_bytes(), CFG.courses)


def _alnum(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", text.casefold())


def _assert_no_identifier(text: str, where: str) -> None:
    for value in EXPECTED["identifiers"]:
        assert value not in text, f"{value!r} found in {where}"
        if any(ch.isdigit() for ch in value) and len(_alnum(value)) >= 6:
            assert _alnum(value) not in _alnum(text), f"{value!r} (digits only) found in {where}"


def _output(rows: list[dict] | None = None) -> dict:
    rows = rows if rows is not None else EXPECTED["courses"]
    return {
        "courses": [
            {
                "name": r["name"],
                "code": r["code"],
                "institution": r["institution"],
                "grade": r["grade"],
                "credits": r["credits"],
                "term": r["term"],
                "lecturers": list(r["lecturers"]),
                "description": "",
            }
            for r in rows
        ],
        "warnings": [],
    }


# ---- text extraction ----------------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", list(FORMATS))
def test_every_format_yields_every_course_line(fmt: str) -> None:
    text = _text(fmt)
    for row in EXPECTED["courses"]:
        assert row["code"] in text and row["name"] in text, (fmt, row["code"])


def test_a_scanned_pdf_is_refused() -> None:
    with pytest.raises(extract.ExtractError) as err:
        extract.extract_text("scan.pdf", (EVAL / "scanned.pdf").read_bytes(), CFG.courses)
    assert str(err.value) == (
        "This PDF has no text layer; OCR is not supported. Export it as text or Word, or paste the text."
    )


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("transcript.xlsx", b"PK\x03\x04rest", "Only .docx and .pdf files"),
        ("transcript.pdf", b"PK\x03\x04 not a pdf", "is not a PDF"),
        ("transcript.docx", b"%PDF-1.4 not a docx", "is not a Word"),
        ("transcript.docx", b"PK\x03\x04 broken zip", "could not be read"),
    ],
)
def test_other_or_broken_files_are_refused(name: str, data: bytes, message: str) -> None:
    with pytest.raises(extract.ExtractError, match=message):
        extract.extract_text(name, data, CFG.courses)


def test_an_oversized_upload_is_refused() -> None:
    data = b"%PDF-" + b"0" * CFG.courses.max_upload_bytes
    with pytest.raises(extract.ExtractError, match="larger than"):
        extract.extract_text("big.pdf", data, CFG.courses)


# ---- identifiers --------------------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", list(FORMATS))
def test_every_identifier_is_stripped_in_every_format_and_layout(fmt: str) -> None:
    stripped = identifiers.strip(_text(fmt), CFG.courses)
    _assert_no_identifier(stripped, fmt)
    assert stripped.count(CFG.courses.removed_marker) >= len(EXPECTED["identifiers"])


@pytest.mark.parametrize("fmt", list(FORMATS))
def test_codes_grades_credits_and_look_alikes_survive(fmt: str) -> None:
    stripped = identifiers.strip(_text(fmt), CFG.courses)
    for value in EXPECTED["keep"]:
        assert value in stripped, (fmt, value)
    for row in EXPECTED["courses"]:
        assert importer.grade_on_row(stripped, row["name"], row["code"], row["grade"]), (fmt, row["code"])
        assert row["code"] in stripped


def test_a_bsn_like_number_failing_the_11_check_is_kept() -> None:
    assert identifiers.passes_bsn_check("123456782") and identifiers.passes_bsn_check("111222333")
    assert not identifiers.passes_bsn_check("123456789")
    out = identifiers.strip("Ref 123456782\nDocument number 123456789\n", CFG.courses)
    assert "123456782" not in out and "123456789" in out


@pytest.mark.parametrize(
    ("text", "gone"),
    [
        ("Student number: 512345\n", "512345"),
        ("STUDENTNUMMER 512345\n", "512345"),
        ("Date of birth - 12 March 2001\n", "12 March 2001"),
        ("Geboortedatum: 12 maart 2001\n", "12 maart 2001"),
        ("Burgerservicenummer (BSN): 123 456 782\n", "123 456 782"),
        ("Student ID\n\nS2034567\n", "S2034567"),  # the value on the next non-empty line
        ("Studentnummer\tGeboortedatum\tOpleiding\n598431\t12-03-2001\tBSc\n", "12-03-2001"),
        ("National ID: AB1234567\n", "AB1234567"),
    ],
)
def test_label_anchored_stripping(text: str, gone: str) -> None:
    out = identifiers.strip(text, CFG.courses)
    assert gone not in out and CFG.courses.removed_marker in out


def test_a_label_without_a_findable_value_stops_the_import() -> None:
    with pytest.raises(identifiers.UnresolvedIdentifier) as err:
        identifiers.strip("Date of birth\nsee the attached letter\n", CFG.courses)
    assert "Date of birth" in str(err.value) and "line 1" in str(err.value)
    assert "Nothing was sent to Claude" in str(err.value)


def test_returned_fields_are_stripped_again() -> None:
    row = dict(_output()["courses"][0], description="Taught to student number 512345 only")
    cleaned = importer.strip_row(row, CFG.courses)
    assert "512345" not in cleaned["description"]


@pytest.mark.parametrize(
    ("name", "clean"),
    [
        ("transcript_512345.pdf", "transcript_[removed].pdf"),
        ("Cijferlijst 12-03-2001.docx", "Cijferlijst [removed].docx"),
        ("record 2001-03-12 final.pdf", "record [removed] final.pdf"),
        ("grades 12.03.2001.pdf", "grades [removed].pdf"),
        ("transcript-2024.pdf", "transcript-2024.pdf"),  # a year is kept
    ],
)
def test_the_original_file_name_loses_number_runs_and_dates(name: str, clean: str) -> None:
    assert identifiers.clean_filename(name, CFG.courses) == clean


# ---- grades are facts: row-anchored -------------------------------------------------------------------


def test_a_grade_must_stand_on_its_own_course_row() -> None:
    text = identifiers.strip(_text("txt"), CFG.courses)
    swap = EXPECTED["swap"]
    marketing = next(r for r in EXPECTED["courses"] if r["code"] == swap["code"])
    assert swap["wrong_grade"] in text  # Corporate Finance's grade appears elsewhere in the document …
    assert not importer.grade_on_row(
        text, marketing["name"], marketing["code"], swap["wrong_grade"]
    )  # … flagged
    assert importer.grade_on_row(text, marketing["name"], marketing["code"], marketing["grade"])


def test_a_course_name_inside_a_longer_one_does_not_lend_its_grade() -> None:
    text = "Mathematics I\t8,0\nMathematics II\t8,5\n"
    assert not importer.grade_on_row(text, "Mathematics I", None, "8,5")
    assert importer.grade_on_row(text, "Mathematics I", None, "8,0")


@pytest.mark.parametrize(("grade", "found"), [("8", False), ("A", False), ("8,5", True), ("A-", True)])
def test_a_grade_is_matched_as_a_whole_value(grade: str, found: bool) -> None:
    text = "FEB1 Course one 8,5\nFIN 2 Course two A-\n"
    code = "FEB1" if grade[0].isdigit() else "FIN 2"
    assert importer.grade_on_row(text, "Course", code, grade) is found


# ---- routes: preview, accept, nothing before confirm --------------------------------------------------


@pytest.fixture
def fake_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def use(output: dict) -> None:
        monkeypatch.setenv("FAKE_CLAUDE_RECORDING", str(write_recording(tmp_path, output, "courses.json")))

    use(_output())
    return use


@pytest.fixture
def calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "fake-calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(path))
    return path


def _upload(client: FormClient, fmt: str):
    name = FORMATS[fmt]
    if fmt == "txt":
        return client.post("/courses/import", data={"pasted": (EVAL / name).read_text(encoding="utf-8")})
    mime = (
        "application/pdf"
        if fmt == "pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return client.post(
        "/courses/import", files={"file": (name, (EVAL / name).read_bytes(), mime)}, data={"pasted": ""}
    )


def _changed(before: dict, after: dict) -> set[str]:
    return {k for k in set(before) | set(after) if before.get(k) != after.get(k)}


@pytest.mark.parametrize("fmt", list(FORMATS))
def test_preview_shows_every_row_writes_nothing_and_sends_only_stripped_text(
    client: FormClient, data_dir: Path, fake_rows, calls: Path, fmt: str
) -> None:
    before = data_snapshot(data_dir)
    res = _upload(client, fmt)
    assert res.status_code == 200
    html = res.text
    assert _changed(before, data_snapshot(data_dir)) <= {
        "logs/",
        "logs/ai_usage.jsonl",
    }  # only the usage line
    for row in EXPECTED["courses"]:
        assert row["code"] in html and row["name"].replace("'", "&#39;") in html
    _assert_no_identifier(html, "the preview page")
    (call,) = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
    sent = json.loads(call["stdin"])
    assert list(sent) == ["text", "institution_hint"]
    _assert_no_identifier(sent["text"], "the payload")
    hidden = dict(form_fields(html, "courses-preview"))
    assert set(hidden) >= {"text", "original_name", "original_sha256"}
    _assert_no_identifier(hidden["text"], "the hidden form state")
    line = json.loads((data_dir / "logs" / "ai_usage.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert (line["action"], line["subject"], line["source"]) == ("import_courses", {}, "app")


def test_an_unfound_or_swapped_grade_is_flagged_and_blocks_its_row(
    client: FormClient, data_dir: Path, fake_rows
) -> None:
    rows = [dict(r) for r in EXPECTED["courses"]]
    swapped = next(r for r in rows if r["code"] == EXPECTED["swap"]["code"])
    swapped["grade"] = EXPECTED["swap"]["wrong_grade"]
    rows[0]["grade"] = "10,0"  # invented: not in the document at all
    fake_rows(_output(rows))
    html = _upload(client, "txt").text
    assert html.count("Grade not found in the document") == 2
    fields = form_fields(html, "courses-preview")
    before = data_snapshot(data_dir)
    index = rows.index(swapped)
    res = client.post("/courses/import/save", data=[*fields, ("accept", str(index))])
    assert res.status_code == 409
    assert data_snapshot(data_dir) == before
    # Correcting the grade in the row makes it acceptable.
    fixed = set_field(fields, f"row-{index}-grade", "6,5")
    assert client.post("/courses/import/save", data=[*fixed, ("accept", str(index))]).status_code == 200
    stored = YAML(typ="safe").load((data_dir / "courses.yaml").read_text(encoding="utf-8"))["courses"]
    assert [(c["code"], c["grade"], c["confirmed"]) for c in stored] == [("FEB12002", "6,5", True)]


def test_a_scanned_pdf_is_refused_by_the_route_and_nothing_runs(
    client: FormClient, data_dir: Path, calls: Path
) -> None:
    before = data_snapshot(data_dir)
    res = client.post(
        "/courses/import",
        files={"file": ("scan.pdf", (EVAL / "scanned.pdf").read_bytes(), "application/pdf")},
    )
    assert res.status_code == 409
    assert "This PDF has no text layer; OCR is not supported." in res.text
    assert data_snapshot(data_dir) == before and not calls.exists()


def test_an_unresolved_label_stops_before_the_model(client: FormClient, data_dir: Path, calls: Path) -> None:
    before = data_snapshot(data_dir)
    res = client.post("/courses/import", data={"pasted": "Date of birth\nsee letter\nFEB1\tCourse\t8,0\n"})
    assert res.status_code == 409
    assert "Nothing was sent to Claude" in res.text
    assert data_snapshot(data_dir) == before and not calls.exists()


def test_a_failed_call_writes_only_its_usage_line(
    client: FormClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "exit")
    before = data_snapshot(data_dir)
    res = client.post(
        "/courses/import", data={"pasted": (EVAL / "transcript.txt").read_text(encoding="utf-8")}
    )
    assert res.status_code == 409 and "Nothing was saved." in res.text
    assert_only_usage_line(
        data_dir, before, ok=False, error="failed", action="import_courses", subject={}, source="app"
    )


def _accept_all(client: FormClient, fmt: str = "pdf"):
    html = _upload(client, fmt).text
    return client.post(
        "/courses/import/save", data=[*form_fields(html, "courses-preview"), ("accept", "all")]
    )


def test_accept_all_writes_confirmed_courses_and_only_stripped_text(
    client: FormClient, data_dir: Path, fake_rows
) -> None:
    res = _accept_all(client)
    assert res.status_code == 200
    stored = YAML(typ="safe").load((data_dir / "courses.yaml").read_text(encoding="utf-8"))["courses"]
    assert len(stored) == 28 and all(c["confirmed"] is True for c in stored)
    assert [(c["code"], c["name"], c["grade"], c["credits"]) for c in stored] == [
        (r["code"], r["name"], r["grade"], r["credits"]) for r in EXPECTED["courses"]
    ]
    sources = list((data_dir / "sources").glob("*.txt"))
    assert len(sources) == 1 and {c["source_document"] for c in stored} == {sources[0].name}
    header = sources[0].read_text(encoding="utf-8").splitlines()[:2]
    sha = hashlib.sha256((EVAL / "transcript.pdf").read_bytes()).hexdigest()
    assert header == ["# Original file: transcript.pdf", f"# sha256: {sha}"]
    assert not list(data_dir.rglob("*.pdf")) and not list(data_dir.rglob("*.docx"))  # never the original
    for path in data_dir.rglob("*"):
        if path.is_file():
            _assert_no_identifier(path.read_text(encoding="utf-8", errors="replace"), str(path))


def test_importing_the_same_rows_again_is_refused_as_duplicates(
    client: FormClient, data_dir: Path, fake_rows
) -> None:
    _accept_all(client)
    before = (data_dir / "courses.yaml").read_bytes()
    html = _upload(client, "pdf").text
    assert html.count("Already imported") == 28
    res = client.post("/courses/import/save", data=[*form_fields(html, "courses-preview"), ("accept", "all")])
    assert res.status_code == 409
    assert (data_dir / "courses.yaml").read_bytes() == before


# ---- professors from the document (US-20) -------------------------------------------------------------


def test_each_lecturer_is_listed_once_with_all_their_courses(config) -> None:
    courses = [
        TranscriptCourse(id=f"c{i:02d}", source_document="t.txt", confirmed=True, **{k: r[k] for k in (
            "name", "code", "institution", "grade", "credits", "term", "lecturers")})
        for i, r in enumerate(EXPECTED["courses"])
    ]  # fmt: skip
    groups = lecturers.group(courses, config.names, config.courses)
    got = {
        f"{g.parsed.surname} {' '.join(g.parsed.initials)}": sorted(c.code for c, _ in g.courses)
        for g in groups
    }
    assert got == {k: sorted(v) for k, v in EXPECTED["lecturers"].items()}
    assert len(groups) == len(EXPECTED["lecturers"])
    berg = next(g for g in groups if g.parsed.surname == "van den Berg")
    assert berg.written == "Prof. dr. A. van den Berg" and "A. van den Berg" in berg.also_written
    smit = next(g for g in groups if g.parsed.surname == "Smit")
    assert smit.listed_first and [first for _, first in smit.courses].count(False) == 2
    scores = [g.best for g in groups]
    assert scores == sorted(scores, reverse=True)  # sorted by best grade
    assert groups[0].parsed.surname == "Ødegaard"  # 9,5 is the best grade


def test_create_professors_preview_writes_nothing_and_offers_links(
    client: FormClient, config, data_dir: Path, fake_rows
) -> None:
    _accept_all(client)
    store = Store.from_config(config)
    store.create_professor(Professor(id="smit", name="Dr. Jan Smit", university="RSM", course="Organisation"))
    source = next((data_dir / "sources").glob("*.txt")).name
    before = data_snapshot(data_dir)
    res = client.post("/courses/import/professors", data={"source": source})
    assert res.status_code == 200
    assert data_snapshot(data_dir) == before
    html = res.text
    assert "Link to Dr. Jan Smit" in html
    assert html.count('name="pick"') >= len(EXPECTED["lecturers"])
    assert " checked" not in html.split('id="professors-preview"', 1)[1]  # nothing ticked
    for written in ("Prof. dr. A. van den Berg", "Dr. L. Ødegaard", "Prof. Daniel O&#39;Connor"):
        assert html.count(written) >= 1


def test_batch_create_and_link_never_overwrite_a_file(
    client: FormClient, config, data_dir: Path, fake_rows
) -> None:
    _accept_all(client)
    hand_made = data_dir / "professors" / "bakker.yaml"
    hand_made.write_text("# a hand-made file the batch must not touch\nnot: valid\n", encoding="utf-8")
    original = hand_made.read_bytes()
    source = next((data_dir / "sources").glob("*.txt")).name
    html = client.post("/courses/import/professors", data={"source": source}).text
    picks = re.findall(r'name="pick" value="([^"]+)"', html)
    wanted = [p for p in picks if p.startswith(("create:bakker", "create:odegaard", "create:van-den-berg"))]
    assert len(wanted) == 3, picks
    res = client.post(
        "/courses/import/professors",
        data=[("source", source), ("step", "create"), *(("pick", p) for p in wanted)],
    )
    assert res.status_code in {200, 303}
    assert hand_made.read_bytes() == original
    created = {
        p.stem: YAML(typ="safe").load(p.read_text(encoding="utf-8"))
        for p in (data_dir / "professors").glob("*.yaml")
        if p.stem not in {"jansen", "whitfield", "bakker"}
    }
    assert set(created) == {"bakker-2", "odegaard", "van-den-berg"}
    berg = created["van-den-berg"]
    assert (berg["name"], berg["title"], berg["surname"], berg.get("email")) == (
        "Prof. dr. A. van den Berg",
        "Professor",
        "van den Berg",
        None,
    )
    stored = {
        c["id"]: c["code"]
        for c in YAML(typ="safe").load((data_dir / "courses.yaml").read_text(encoding="utf-8"))["courses"]
    }
    assert sorted(stored[i] for i in berg["course_ids"]) == sorted(EXPECTED["lecturers"]["van den Berg A"])
    assert created["odegaard"]["title"] == "Dr."
    # Running the same create again creates nothing new: each lecturer now matches the created professor.
    again = client.post(
        "/courses/import/professors",
        data=[("source", source), ("step", "create"), *(("pick", p) for p in wanted)],
    )
    assert again.status_code == 409
    assert len(list((data_dir / "professors").glob("*.yaml"))) == 6


# ---- review fixes (ai-output-reviewer): real-world labels, identity- and column-anchored checks ------


@pytest.mark.parametrize(
    ("text", "gone"),
    [
        ("DOB: 03/12/2001\n", "03/12/2001"),
        ("Geboren op 12 maart 2001\n", "12 maart 2001"),
        ("SSN 123-45-6789\n", "123-45-6789"),
        ("Student nr. 512345\n", "512345"),
        ("Matriculation number 512345\n", "512345"),
        ("Registration number: 512345\n", "512345"),
    ],
)
def test_real_world_identifier_labels_are_stripped(text: str, gone: str) -> None:
    out = identifiers.strip(text, CFG.courses)
    assert gone not in out and CFG.courses.removed_marker in out, out


def test_a_short_labelled_value_is_removed_and_never_takes_the_next_line() -> None:
    out = identifiers.strip("Student number: 4123\nFEB11001\tMathematics I\t8,0\n", CFG.courses)
    assert "4123" not in out
    assert "FEB11001\tMathematics I\t8,0" in out  # the course line is untouched


def test_the_name_and_the_code_must_be_on_the_same_row() -> None:
    text = identifiers.strip(_text("txt"), CFG.courses)
    row = {"name": "Mathematics I", "code": "FEB11002", "grade": "7,5", "credits": 6.0, "term": None}
    assert importer.COURSE_NOT_FOUND in importer.row_problems(row, text, [])


@pytest.mark.parametrize("grade", ["4", "2021", "1", "2022"])  # the credits, the years and the block number
def test_a_grade_equal_to_the_rows_credits_or_term_is_flagged(grade: str) -> None:
    text = identifiers.strip(_text("txt"), CFG.courses)
    row = {
        "name": "Mathematics I",
        "code": "FEB11001",
        "grade": grade,
        "credits": 4.0,
        "term": "2021-2022 block 1",
    }
    assert importer.GRADE_NOT_FOUND in importer.row_problems(row, text, [])


def test_a_grade_that_really_equals_the_credits_still_passes() -> None:
    text = "C1\tCourse one\t6\t6\t2024 block 2\n"
    row = {"name": "Course one", "code": "C1", "grade": "6", "credits": 6.0, "term": "2024 block 2"}
    assert importer.row_problems(row, text, []) == []


def test_credits_must_stand_on_the_row() -> None:
    text = identifiers.strip(_text("txt"), CFG.courses)
    row = dict(next(r for r in EXPECTED["courses"] if r["code"] == "FEB11002"), credits=5.0)
    assert importer.CREDITS_NOT_FOUND in importer.row_problems(row, text, [])
    good = next(r for r in EXPECTED["courses"] if r["code"] == "FEB11002")
    assert importer.row_problems(good, text, []) == []
    nfu = next(r for r in EXPECTED["courses"] if r["code"] == "FIN 3010")  # "3.0" printed, 3.0 returned
    assert importer.row_problems(nfu, text, []) == []


# ---- review fixes (data-integrity-reviewer): identifier stripping fails closed --------------------------


@pytest.mark.parametrize(
    ("text", "gone"),
    [
        ("Date of birth: 2001/03/12\n", ["2001", "03/12"]),
        ("Date of birth: 2001.03.12\n", ["2001", "03.12"]),
        ("Date of birth: 12th March 2001\n", ["12th", "2001"]),
        ("Date of birth: March 12th, 2001\n", ["12th", "2001"]),
        ("Date of birth: 3rd of March, 2001\n", ["3rd", "2001"]),
        ("Geboortedatum: 12-mrt-2001\n", ["12-mrt", "2001"]),
        ("Student identification number or code (if available): 512345\n", ["512345"]),
        ("Stud.nr.: 512345\n", ["512345"]),
    ],
)
def test_more_date_and_label_forms_are_stripped_whole(text: str, gone: list[str]) -> None:
    out = identifiers.strip(text, CFG.courses)
    for part in gone:
        assert part not in out, out


def test_a_table_value_row_is_matched_by_column() -> None:
    text = "Studentnummer\tOpleiding\tGeboortedatum\n598431\t56966 BSc Econometrie\t12-03-2001\n"
    out = identifiers.strip(text, CFG.courses)
    assert "598431" not in out and "12-03-2001" not in out
    assert "56966 BSc Econometrie" in out  # the programme code in the middle column is kept


@pytest.mark.parametrize("text", ["Date of birth: 12/3 2001\n", "Student number: 512 / 345 / 7\n"])
def test_a_value_the_app_cannot_take_whole_stops_the_import(text: str) -> None:
    with pytest.raises(identifiers.UnresolvedIdentifier):
        identifiers.strip(text, CFG.courses)


# ---- review fixes (data-integrity-reviewer): links, CRLF, write order, batch duplicates ------------------


def test_an_unchanged_edit_keeps_course_links_the_form_cannot_show(
    client: FormClient, config, data_dir: Path
) -> None:
    store = Store.from_config(config)
    prof = next(p for p in store.load_professors()[0] if p.id == "jansen")
    prof.course_ids = ["gone-course"]  # linked, but not (or no longer) in courses.yaml
    store.save_professor(prof)
    path = data_dir / "professors" / "jansen.yaml"
    before = path.read_bytes()
    fields = form_fields(client.get("/recommenders/jansen/edit").text, "professor-form")
    assert client.post("/recommenders/jansen", data=fields).status_code == 303
    assert path.read_bytes() == before


def test_unticking_a_shown_course_unlinks_only_that_one(
    client: FormClient, config, data_dir: Path, fake_rows
) -> None:
    _accept_all(client)
    store = Store.from_config(config)
    ids = [c.id for c in store.load_courses().courses][:2]
    prof = next(p for p in store.load_professors()[0] if p.id == "jansen")
    prof.course_ids = [*ids, "gone-course"]
    store.save_professor(prof)
    fields = form_fields(client.get("/recommenders/jansen/edit").text, "professor-form")
    fields = [(k, v) for k, v in fields if not (k == "course_ids" and v == ids[0])]
    assert client.post("/recommenders/jansen", data=fields).status_code == 303
    stored = YAML(typ="safe").load((data_dir / "professors" / "jansen.yaml").read_text(encoding="utf-8"))
    assert stored["course_ids"] == [ids[1], "gone-course"]


def test_browser_line_breaks_are_stored_as_lf(client: FormClient, data_dir: Path, fake_rows) -> None:
    html = _upload(client, "txt").text
    fields = form_fields(html, "courses-preview")
    fields = [(k, v.replace("\n", "\r\n")) if k == "text" else (k, v) for k, v in fields]
    fields = set_field(fields, "row-0-description", "line one\r\nline two")
    assert client.post("/courses/import/save", data=[*fields, ("accept", "0")]).status_code == 200
    raw = (data_dir / "courses.yaml").read_bytes()
    assert b"\r" not in raw and b"\r\n" not in raw
    source = next((data_dir / "sources").glob("*.txt")).read_bytes()
    assert b"\r" not in source


def test_a_bad_hash_or_name_in_the_form_never_reaches_the_header(
    client: FormClient, data_dir: Path, fake_rows
) -> None:
    html = _upload(client, "txt").text
    fields = set_field(form_fields(html, "courses-preview"), "original_sha256", "abc\n# injected")
    fields = set_field(fields, "original_name", "x\n# injected.pdf")
    assert client.post("/courses/import/save", data=[*fields, ("accept", "0")]).status_code == 200
    header = next((data_dir / "sources").glob("*.txt")).read_text(encoding="utf-8").split("\n\n", 1)[0]
    assert "injected" not in header.replace("x # injected.pdf", "") and header.count("\n") == 2


def test_an_unreadable_courses_file_writes_no_source(client: FormClient, data_dir: Path, fake_rows) -> None:
    html = _upload(client, "txt").text
    (data_dir / "courses.yaml").write_text("courses: [unclosed\n", encoding="utf-8")
    res = client.post("/courses/import/save", data=[*form_fields(html, "courses-preview"), ("accept", "0")])
    assert res.status_code == 409
    assert not (data_dir / "sources").exists()


def test_the_same_course_twice_in_one_document_is_saved_once(
    client: FormClient, data_dir: Path, fake_rows
) -> None:
    rows = [dict(r) for r in EXPECTED["courses"]]
    fake_rows(_output([*rows, rows[0]]))
    html = _upload(client, "txt").text
    assert "Same course as row 1" in html
    client.post("/courses/import/save", data=[*form_fields(html, "courses-preview"), ("accept", "all")])
    stored = YAML(typ="safe").load((data_dir / "courses.yaml").read_text(encoding="utf-8"))["courses"]
    assert len(stored) == 28


def test_linking_to_a_professor_deleted_since_the_preview_is_refused(
    client: FormClient, config, data_dir: Path, fake_rows
) -> None:
    _accept_all(client)
    Store.from_config(config).create_professor(Professor(id="smit", name="Dr. Jan Smit", university="RSM"))
    source = next((data_dir / "sources").glob("*.txt")).name
    (data_dir / "professors" / "smit.yaml").unlink()
    res = client.post(
        "/courses/import/professors",
        data=[("source", source), ("step", "create"), ("pick", "link:smit-j:smit")],
    )
    assert res.status_code == 409


# ---- review round 2 (data-integrity): key/value cells, more date shapes, labels, credits, anchoring ------


@pytest.mark.parametrize(
    ("text", "gone", "kept"),
    [
        ("Student number\t512345\nFEB12003\tCorporate Finance\t8,5\t6\n", ["512345"], ["FEB12003"]),
        ("Student ID\t1234567\n01\tECON 301\tA\t3.0\n", ["1234567"], ["01\tECON 301"]),
        ("Name\tNoor Example\nDate of birth\t12-03-2001\n", ["12-03-2001"], ["Noor Example"]),
    ],
)
def test_a_value_in_the_cell_next_to_its_label_is_stripped(
    text: str, gone: list[str], kept: list[str]
) -> None:
    out = identifiers.strip(text, CFG.courses)
    for v in gone:
        assert v not in out, out
    for v in kept:
        assert v in out, out


@pytest.mark.parametrize(
    ("text", "gone"),
    [
        ("Birth Date: 12-MAR-01\n", ["12-MAR-01", "MAR-01"]),
        ("Date of birth: 2001-Mar-12\n", ["Mar-12", "2001"]),
        ("Date of birth: 12 Mar 01\n", ["Mar 01", "12 Mar"]),
        ("Geboortedatum: 12 maart '01\n", ["maart", "'01"]),
        ("Date of birth: 12.III.2001\n", ["III", "2001"]),
        ("Date of birth: March 2001\n", ["March", "2001"]),
    ],
)
def test_short_year_month_word_and_roman_dates_are_stripped_whole(text: str, gone: list[str]) -> None:
    try:
        out = identifiers.strip(text, CFG.courses)
    except identifiers.UnresolvedIdentifier:
        return  # stopping is also safe: nothing is sent
    for v in gone:
        assert v not in out, out


@pytest.mark.parametrize(
    "text",
    [
        "Student number: 512345, previously 498765\n",  # a second number would leak
        "Student ID\nsee letter FEB11001\n",  # the next line doesn't start with the value
    ],
)
def test_ambiguous_values_stop_the_import(text: str) -> None:
    with pytest.raises(identifiers.UnresolvedIdentifier):
        identifiers.strip(text, CFG.courses)


@pytest.mark.parametrize(
    ("text", "gone"),
    [
        ("Geb. datum: 12-03-2001\n", "12-03-2001"),
        ("Collegekaartnummer: 512345\n", "512345"),
        ("Student #: 512345\n", "512345"),
        ("Emplid: 1234567\n", "1234567"),
        ("UIN: 123456789\n", "123456789"),
        ("Banner ID: N00412345\n", "N00412345"),
        ("Campus ID: 1234567\n", "1234567"),
        ("Social Security No. 123-45-6789\n", "123-45-6789"),
        ("Born 12 March 2001\n", "12 March 2001"),
        ("ref 123-45-6789 on file\n", "123-45-6789"),  # an SSN shape is removed even without a label
    ],
)
def test_more_real_world_labels(text: str, gone: str) -> None:
    out = identifiers.strip(text, CFG.courses)
    assert gone not in out, out


def test_credits_with_trailing_zeros_are_found() -> None:
    text = "FIN 3010\tFixed Income Securities\tA\t3.00\tFall 2023\n"
    row = {
        "name": "Fixed Income Securities",
        "code": "FIN 3010",
        "grade": "A",
        "credits": 3.0,
        "term": "Fall 2023",
    }
    assert importer.row_problems(row, text, []) == []


def test_the_term_must_be_on_the_row_when_the_course_was_taken_twice() -> None:
    text = "FEB1\tCourse one\t4,5\t6\t2022-2023\nFEB1\tCourse one\t7,0\t6\t2023-2024\n"
    row = {"name": "Course one", "code": "FEB1", "grade": "4,5", "credits": 6.0, "term": "2023-2024"}
    assert importer.GRADE_NOT_FOUND in importer.row_problems(row, text, [])
    assert importer.row_problems(dict(row, grade="7,0"), text, []) == []


def test_grade_and_credits_are_read_from_their_heading_columns() -> None:
    text = "Code\tCourse\tGrade\tCredits\nFEB12003\tCorporate Finance\t8,5\t6\n"
    row = {"name": "Corporate Finance", "code": "FEB12003", "grade": "6", "credits": 8.5, "term": None}
    problems = importer.row_problems(row, text, [])
    assert importer.GRADE_NOT_FOUND in problems and importer.CREDITS_NOT_FOUND in problems
    assert importer.row_problems(dict(row, grade="8,5", credits=6.0), text, []) == []


def test_accepting_again_from_an_old_page_reuses_the_source_file(
    client: FormClient, data_dir: Path, fake_rows
) -> None:
    html = _upload(client, "txt").text
    fields = form_fields(html, "courses-preview")
    client.post("/courses/import/save", data=[*fields, ("accept", "0")])
    client.post("/courses/import/save", data=[*fields, ("accept", "1")])  # the old page (Back), source empty
    assert len(list((data_dir / "sources").glob("*.txt"))) == 1
    stored = YAML(typ="safe").load((data_dir / "courses.yaml").read_text(encoding="utf-8"))["courses"]
    assert len({c["source_document"] for c in stored}) == 1


def test_an_unticked_link_stays_unticked_after_a_validation_error(
    client: FormClient, config, data_dir: Path, fake_rows
) -> None:
    _accept_all(client)
    store = Store.from_config(config)
    ids = [c.id for c in store.load_courses().courses][:2]
    prof = next(p for p in store.load_professors()[0] if p.id == "jansen")
    prof.course_ids = ids
    store.save_professor(prof)
    fields = form_fields(client.get("/recommenders/jansen/edit").text, "professor-form")
    fields = [(k, v) for k, v in fields if not (k == "course_ids" and v == ids[0])]
    fields = set_field(fields, "name", "")  # a validation error
    html = client.post("/recommenders/jansen", data=fields).text
    again = form_fields(html, "professor-form")
    assert ("course_ids_shown", ids[0]) in again and ("course_ids", ids[0]) not in again


def test_a_short_value_leading_the_next_line_is_removed_and_the_course_kept() -> None:
    out = identifiers.strip("Student ID\n4123\nFEB11001\tMathematics I\t8,0\n", CFG.courses)
    assert "4123" not in out and "FEB11001\tMathematics I\t8,0" in out
