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
    # session 3 (disclosed, DECISIONS #76): "Document number" became a label, so the failing number stands
    # after a word that is not one; the 11-check is what keeps it
    out = identifiers.strip("Ref 123456782\nCertificate 123456789\n", CFG.courses)
    assert "123456782" not in out and "123456789" in out


@pytest.mark.parametrize(
    ("text", "gone"),
    [
        ("Diploma register 2023-004512\n", "2023-004512"),
        ("Diploma register number: 2023-004512\n", "2023-004512"),
        ("Diplomaregisternummer: 2023-004512\n", "2023-004512"),
        ("Document number 123456789\n", "123456789"),  # removed by its label, though it fails the 11-check
        ("Documentnummer: AB1234567\n", "AB1234567"),
        ("Diploma register\n2023-004512\n", "2023-004512"),  # the value on the next line
    ],
)
def test_diploma_register_and_document_numbers_are_identifiers(text: str, gone: str) -> None:
    """User decision (session 3, DECISIONS #76): the fixture's look-alikes are identifiers; a year-serial
    register number ("2023-004512") is one value, taken whole."""
    out = identifiers.strip(text, CFG.courses)
    assert gone not in out and CFG.courses.removed_marker in out, out


def test_the_fixture_register_line_loses_both_numbers() -> None:
    out = identifiers.strip("Diploma register 2023-004512 - Document number 123456789\n", CFG.courses)
    assert out == "Diploma register [removed] - Document number [removed]\n"


@pytest.mark.parametrize("text", ["Student ID\n5123-45\n", "Diploma register 2023-0045\n"])
def test_a_short_hyphenated_value_still_stops(text: str) -> None:
    """The year-serial shape needs 5+ digits after the hyphen, so "5123-45" (F1) still stops, and a 4-digit
    serial is not mistaken for an academic year range ("2021-2022")."""
    with pytest.raises(identifiers.UnresolvedIdentifier):
        identifiers.strip(text, CFG.courses)


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


def _read(client: FormClient, fmt: str):
    """Step 1, Read courses: extract and strip, then the review page (no AI call; DECISIONS #70)."""
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


def _send(client: FormClient, review_html: str):
    """Step 2, Send to Claude: the review form as a browser submits it."""
    return client.post("/courses/import/send", data=form_fields(review_html, "courses-send"))


def _upload(client: FormClient, fmt: str):
    """Read, then Send (session 2: the review step comes between; disclosed helper change, DECISIONS #70)."""
    review = _read(client, fmt)
    assert review.status_code == 200, review.text[:500]
    return _send(client, review.text)


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
    res = _upload(client, "txt")  # Read, then Send (disclosed: the call now happens at Send, #70)
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
    # changed with the user's request (DECISIONS #95): a row matching a stored course is offered "Merge into
    # <course>" instead of being blocked as "Already imported"; Accept all still writes nothing
    assert html.count('name="accept" value="merge-') == 28 and "Already imported" not in html
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
        ("Stud.nr.: 512345\n", ["512345"]),
    ],
)
def test_more_date_and_label_forms_are_stripped_whole(text: str, gone: list[str]) -> None:
    out = identifiers.strip(text, CFG.courses)
    for part in gone:
        assert part not in out, out


def test_words_between_a_label_and_its_value_stop() -> None:
    """Changed with disclosure (DECISIONS #84, user decision "known layouts only"): this case was stripped
    before; words between the label and its value are outside "Label: value", so the import stops."""
    with pytest.raises(identifiers.UnresolvedIdentifier):
        identifiers.strip("Student identification number or code (if available): 512345\n", CFG.courses)


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
    # changed with disclosure (DECISIONS #86): an invented description is now flagged (rule 15), so the
    # multi-line field is row 0's own lecturers, posted with CRLF as a browser does
    fields = set_field(fields, "row-0-lecturers", "Prof. dr. A. van den Berg\r\nDr. J. Smit")
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
    page = client.post("/courses/import/professors", data={"source": source}).text
    pick = re.search(r'value="(link:[^"]+:smit)"', page).group(1)  # the key is no longer a bare slug (#105)
    (data_dir / "professors" / "smit.yaml").unlink()
    res = client.post(
        "/courses/import/professors",
        data=[("source", source), ("step", "create"), ("pick", pick)],
    )
    assert res.status_code == 409 and "not valid" in res.text  # the lecturer is there, the professor isn't


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


# ---- format characters (session 2, DECISIONS #69) -------------------------------------------------------
# A zero-width space, soft hyphen, joiner or byte-order mark inside a label hid it from the label match, and
# one between a value's digits split the value. Cf characters are removed and NBSP / narrow NBSP read as
# spaces before any matching; a label whose value can't be taken whole still stops the import.

ZWSP, SHY, ZWNJ, ZWJ, BOM, NBSP, NNBSP = "​", "­", "‌", "‍", "﻿", " ", " "


@pytest.mark.parametrize(
    ("text", "gone"),
    [
        (f"Student{ZWSP} number: 512345\n", "512345"),
        (f"Stu{SHY}dent number: 512345\n", "512345"),
        (f"Student{ZWSP}nummer: 512345\n", "512345"),
        (f"Student{ZWNJ}nummer: 512345\n", "512345"),
        (f"Student{ZWJ}nummer: 512345\n", "512345"),
        (f"{BOM}Studentnummer: 512345\n", "512345"),
        (f"Student{NBSP}number: 512345\n", "512345"),
        (f"Date of{NNBSP}birth: 12 March 2001\n", "12 March 2001"),
        (f"Geboorte{SHY}datum\n12{ZWSP}-03-2001\n", "12-03-2001"),  # label and next-line value
        (f"Student number: 51{ZWSP}2345\n", "512345"),  # a value split by a zero-width space
        (f"Studentnummer\t{ZWSP}Geboorte{SHY}datum\n598431\t12-03-2001\n", "12-03-2001"),  # table
    ],
)
def test_format_characters_never_hide_a_label_or_split_a_value(text: str, gone: str) -> None:
    out = identifiers.strip(text, CFG.courses)
    assert gone not in out and _alnum(gone) not in _alnum(out), out
    assert CFG.courses.removed_marker in out
    assert not any(ch in out for ch in (ZWSP, SHY, ZWNJ, ZWJ, BOM)), repr(out)


@pytest.mark.parametrize(
    "text",
    [
        f"Student{ZWSP}number: 12/3 2001\n",  # recognised now, and its value can't be taken whole
        f"Date of{SHY} birth\nsee the attached letter\n",
        f"Student{ZWSP} ID\nsee letter FEB11001\n",
    ],
)
def test_a_label_with_format_characters_still_fails_closed(text: str) -> None:
    with pytest.raises(identifiers.UnresolvedIdentifier):
        identifiers.strip(text, CFG.courses)


def test_returned_fields_with_format_characters_are_stripped() -> None:
    row = {**_output()["courses"][0], "description": f"Stu{SHY}dent number 512345 on file"}
    assert "512345" not in importer.strip_row(row, CFG.courses)["description"]


def test_the_file_name_loses_a_number_split_by_a_zero_width_space() -> None:
    assert "512345" not in _alnum(identifiers.clean_filename(f"transcript 51{ZWSP}2345.pdf", CFG.courses))


# ---- review before sending (session 2, DECISIONS #70) ---------------------------------------------------
# Read courses extracts and strips the text and shows it for review, each removed value highlighted; nothing
# is sent to Claude until Send to Claude. The review form carries only the stripped text (plus the cleaned
# file name and the original's sha256); Send strips it again (strict) before the call.

MARK = '<mark class="removed">'


@pytest.mark.parametrize("fmt", list(FORMATS))
def test_read_shows_the_stripped_text_for_review_and_sends_nothing(
    client: FormClient, data_dir: Path, calls: Path, fmt: str
) -> None:
    before = data_snapshot(data_dir)
    res = _read(client, fmt)
    assert res.status_code == 200
    assert not calls.exists()  # no AI call before Send
    assert data_snapshot(data_dir) == before  # nothing written, not even a usage line
    html = res.text
    _assert_no_identifier(html, "the review page")
    fields = form_fields(html, "courses-send")
    assert sorted(k for k, _ in fields) == ["original_name", "original_sha256", "text"]
    text = dict(fields)["text"]
    assert text == identifiers.strip(_text(fmt), CFG.courses)  # exactly the stripped text
    marker = CFG.courses.removed_marker
    assert text.count(marker) > 0 and html.count(MARK + marker + "</mark>") == text.count(marker)
    assert re.search(r'<button[^>]*type="submit"[^>]*>Send to Claude</button>', html)
    assert re.search(r'<a[^>]*href="/courses/import"[^>]*>Cancel</a>', html)
    assert 'id="courses-preview"' not in html


def test_send_calls_claude_once_with_the_reviewed_text(
    client: FormClient, data_dir: Path, fake_rows, calls: Path
) -> None:
    review = _read(client, "docx").text
    text = dict(form_fields(review, "courses-send"))["text"]
    res = _send(client, review)
    assert res.status_code == 200 and 'id="courses-preview"' in res.text
    (call,) = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
    assert json.loads(call["stdin"])["text"] == text


@pytest.mark.parametrize("fmt", list(FORMATS))
def test_stripping_stripped_text_again_changes_nothing(fmt: str) -> None:
    """Send strips again strictly in the Send mode, where a marker may fill a label's value slot (#73)."""
    once = identifiers.strip(_text(fmt), CFG.courses)
    assert identifiers.strip(once, CFG.courses, marker_fills_slot=True) == once


@pytest.mark.parametrize(
    "text",
    [
        "Student ID\n\nS2034567\nFEB1\tCourse\t8,0\n",
        "Studentnummer\tGeboortedatum\tOpleiding\n598431\t12-03-2001\tBSc\n",
        "Student number\t512345\n",
        "Date of birth - 12 March 2001\n",
    ],
)
def test_every_label_layout_strips_idempotently(text: str) -> None:
    once = identifiers.strip(text, CFG.courses)
    assert identifiers.strip(once, CFG.courses, marker_fills_slot=True) == once


def test_send_strips_a_tampered_text_again(client: FormClient, fake_rows, calls: Path) -> None:
    review = _read(client, "txt").text
    tampered = "Student number: 512345\nFEB1\tCourse\t8,0\n"
    client.post("/courses/import/send", data=set_field(form_fields(review, "courses-send"), "text", tampered))
    (call,) = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
    assert "512345" not in call["stdin"]


def test_a_failed_send_shows_the_review_again_with_the_error(
    client: FormClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "exit")
    review = _read(client, "txt").text
    res = _send(client, review)
    assert res.status_code == 409 and "Nothing was saved." in res.text
    assert form_fields(res.text, "courses-send") == form_fields(review, "courses-send")  # Send can be retried


# ---- the marker is never a value; extra ID-shaped values after lone labels stop (user decision, #73) -------
# At Read the marker "[removed]" never counts as a value. On a value line after k lone labels (no tabs: the
# values must lead the line), if more ID-shaped tokens (5+ digits, or a date) stand on it than labels, the app
# can't tell which one is the identifier, so the import stops (fail closed). At Send (the text was stripped
# at Read) a marker may fill a label's value slot, but is never an ID-shaped token: a number after it stops.


@pytest.mark.parametrize(
    "text",
    [
        "Student number\n[removed] 512345\n",
        "Student ID\n598431 512345\n",
        "Student number: [removed]\n",  # the marker is not a value at Read
        "Studentnummer Geboortedatum\n598431 12-03-2001 704512\n",  # 3 ID-shaped tokens, 2 labels
    ],
)
def test_a_marker_or_an_extra_id_shaped_value_stops_the_import(text: str) -> None:
    with pytest.raises(identifiers.UnresolvedIdentifier) as err:
        identifiers.strip(text, CFG.courses)
    assert "Nothing was sent to Claude" in str(err.value)


@pytest.mark.parametrize(
    ("text", "gone"),
    [
        ("Student ID\n\nS2034567\n", "S2034567"),
        ("Student ID\nS2034567 BSc Econometrics 2021\n", "S2034567"),  # a year has 4 digits: not ID-shaped
        ("Studentnummer Geboortedatum\n598431 12-03-2001 BSc\n", "12-03-2001"),
        ("Student number: 512345\n", "512345"),
    ],
)
def test_single_value_cases_still_strip(text: str, gone: str) -> None:
    out = identifiers.strip(text, CFG.courses)
    assert gone not in out and CFG.courses.removed_marker in out


@pytest.mark.parametrize(
    "text",
    [
        "Student number\n[removed] 512345\n",
        "Student number: [removed] 512345\n",
        "Student number: [removed], previously 512345\n",
    ],
)
def test_at_send_a_number_after_the_marker_still_stops(text: str) -> None:
    with pytest.raises(identifiers.UnresolvedIdentifier):
        identifiers.strip(text, CFG.courses, marker_fills_slot=True)


def test_a_document_that_contains_the_marker_text_is_never_trusted_at_read() -> None:
    with pytest.raises(identifiers.UnresolvedIdentifier):
        identifiers.strip("Date of birth [removed]\nFEB1\tCourse\t8,0\n", CFG.courses)


# ---- data-integrity review round 3 (DECISIONS #74) ------------------------------------------------------
# F1: a next-line value is taken whole (nothing number- or date-like right after it); F4: after the labels'
# values, nothing ID-shaped may remain on that line (Read and Send alike); F3: a bracket after a label holds
# no digits; F2: every default-ignorable character is removed and every space character reads as a space.


@pytest.mark.parametrize("send", [False, True])
@pytest.mark.parametrize(
    "text",
    [
        "Date of birth\n12/3 2001\n",  # F1
        "Student ID\n5123-45\n",  # F1
        "Student ID\n2019 512345\n",  # F1
        "Student ID\n4123 BSc Econometrics 512345\n",  # F4
    ],
)
def test_review_round3_values_not_taken_whole_stop_the_import(text: str, send: bool) -> None:
    with pytest.raises(identifiers.UnresolvedIdentifier) as err:
        identifiers.strip(text, CFG.courses, marker_fills_slot=send)
    assert "512345" not in str(err.value)  # the message never echoes a number


@pytest.mark.parametrize(
    "text",
    ["Student number\n[removed] 4123\n", "Date of birth\n[removed]/3 2001\n"],
)
def test_review_round3_at_send_a_marker_never_hides_what_follows(text: str) -> None:
    with pytest.raises(identifiers.UnresolvedIdentifier):
        identifiers.strip(text, CFG.courses, marker_fills_slot=True)


def test_review_round3_a_bracket_with_digits_is_not_swallowed_by_the_label() -> None:
    out = identifiers.strip("Student ID (512345): 598431\n", CFG.courses, strict=False)
    assert "512345" not in out
    with pytest.raises(identifiers.UnresolvedIdentifier):
        identifiers.strip("Student ID (512345): 598431\n", CFG.courses)  # two values: fail closed
    assert (
        "(BSN)"
        not in identifiers.strip("Burgerservicenummer (BSN): 123 456 782\n", CFG.courses).split("[removed]")[
            1
        ]
    )  # a bracket without digits still belongs to the label


@pytest.mark.parametrize(
    ("text", "gone"),
    [
        ("Student͏number: 512345\n", "512345"),  # combining grapheme joiner
        ("Student️number: 512345\n", "512345"),  # variation selector
        ("Studentㅤnumber: 512345\n", "512345"),  # Hangul filler
        ("Student number\n51 2345\n", "512345"),  # thin space inside the value
        ("Student number: 512345\n", "512345"),  # figure space in the label
    ],
)
def test_review_round3_default_ignorables_and_spaces(text: str, gone: str) -> None:
    out = identifiers.strip(text, CFG.courses)
    assert _alnum(gone) not in _alnum(out), repr(out)


@pytest.mark.parametrize("send", [False, True])
def test_review_round3_a_bracketed_number_is_the_value_and_the_next_line_stays(send: bool) -> None:
    """F3: "(512345)" is no longer part of the label, so it is the label's value on its own line; the course
    code on the next line stays (before, the code was removed and 512345 kept)."""
    text = "Student number (512345)\nFEB11010 Microeconomics\n"
    out = identifiers.strip(text, CFG.courses, marker_fills_slot=send)
    assert "512345" not in out and "FEB11010 Microeconomics" in out


# ---- ui review round 3, F1: Check again names the problem it lands on (session 3, DECISIONS #75) --------


def _tag(html: str, element_id: str) -> str:
    match = re.search(rf'<[a-z]+\b[^>]*\bid="{re.escape(element_id)}"[^>]*>', html)
    assert match, f"no element with id {element_id}"
    return match.group(0)


def _attr(tag: str, name: str) -> str | None:
    match = re.search(rf'\b{name}="([^"]*)"', tag)
    return match.group(1) if match else None


def _check_again_with_row_20_wrong(client: FormClient, fake_rows) -> str:
    rows = [dict(r) for r in EXPECTED["courses"]]
    fake_rows(_output(rows))
    fields = form_fields(_upload(client, "txt").text, "courses-preview")
    fields = set_field(fields, "row-20-grade", "1,0")  # a grade the document doesn't give
    res = client.post("/courses/import/save", data=[*fields, ("accept", "check")])
    assert res.status_code == 200
    return res.text


def test_check_again_announces_the_course_and_problem_it_lands_on(client: FormClient, fake_rows) -> None:
    html = _check_again_with_row_20_wrong(client, fake_rows)
    name = EXPECTED["courses"][20]["name"]
    main = re.search(r'<div class="main-inner[^"]*"[^>]*>', html).group(0)
    assert _attr(main, "data-focus") == "row-20"
    announce = _attr(main, "data-announce") or ""
    assert "Course 21" in announce and "Grade not found in the document" in announce, announce
    # a visible notice at the top names the course and links to it (works without JS)
    notice = re.search(r'<p class="notice notice-problem"[^>]*>(.*?)</p>', html, re.S)
    assert notice, "no problem notice at the top"
    assert 'href="#row-20"' in notice.group(1) and "Grade not found in the document" in notice.group(1)
    assert name.replace("'", "&#39;") in notice.group(1)
    assert html.index("notice-problem") < html.index('id="row-0"')


def test_a_refusal_is_tied_to_its_field_and_to_the_blocked_accept_button(
    client: FormClient, fake_rows
) -> None:
    html = _check_again_with_row_20_wrong(client, fake_rows)
    grade = _tag(html, "row-20-grade")
    assert _attr(grade, "aria-invalid") == "true"
    described = (_attr(grade, "aria-describedby") or "").split()
    assert described, grade
    refusal = re.search(rf'<p class="refusal" id="{described[0]}">([^<]*)</p>', html)
    assert refusal and refusal.group(1) == "Grade not found in the document"
    accept = re.search(r'<button[^>]*name="accept" value="20"[^>]*>', html)
    assert accept and described[0] in (_attr(accept.group(0), "aria-describedby") or "").split()
    # the refusal stands at the top of its card, right under the legend (visible with the course's name)
    card = html[html.index('id="row-20"') :]
    assert card.index('class="refusal"') < card.index('id="row-20-name"')
    # a course without a problem has no dangling references
    clean = re.search(r'<button[^>]*name="accept" value="19"[^>]*>', html)
    assert clean and _attr(clean.group(0), "aria-describedby") is None


# ---- user decision (session 3, DECISIONS #77): a tabbed value row with more cells than its label row ---


@pytest.mark.parametrize(
    "text",
    [
        "Studentnummer\tGeboortedatum\n598431\t12-03-2001\t512345\n",  # a number in a cell no label heads
        "Studentnummer\tOpleiding\n598431\tBSc\t2001-03-12\n",  # a date there
        "Student ID\tProgramme\nS2034567\tBSc\tAB1234567\n",
    ],
)
def test_an_id_shaped_token_in_an_unheaded_cell_stops_the_import(text: str) -> None:
    with pytest.raises(identifiers.UnresolvedIdentifier) as err:
        identifiers.strip(text, CFG.courses)
    assert "Nothing was sent to Claude" in str(err.value)


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        ("Studentnummer\tOpleiding\n598431\tBSc\tEconomics\n", "Economics"),  # words only: no identifier
        ("Studentnummer\tOpleiding\n598431\tBSc\t2021\n", "2021"),  # fewer than 5 digits: not ID-shaped
        ("Studentnummer\tOpleiding\n598431\t56966 BSc Economie\n", "56966"),  # headed column: as before
    ],
)
def test_an_unheaded_cell_without_an_id_shaped_token_still_imports(text: str, kept: str) -> None:
    out = identifiers.strip(text, CFG.courses)
    assert "598431" not in out and kept in out


def test_in_send_mode_a_marker_in_an_unheaded_cell_stops() -> None:
    text = "Studentnummer\tOpleiding\n[removed]\tBSc\t[removed]\n"
    with pytest.raises(identifiers.UnresolvedIdentifier):
        identifiers.strip(text, CFG.courses, marker_fills_slot=True)


# ---- ui review round 3, W2 and W3 (session 3, DECISIONS #75) ---


def test_the_review_says_so_when_nothing_was_removed(client: FormClient) -> None:
    """W2: no "0 values … are replaced by the highlighted [removed]" with a sample marker that isn't there."""
    res = client.post("/courses/import", data={"pasted": "FEB11001\tMathematics I\t8,0\t4\tFall 2023\n"})
    assert res.status_code == 200
    assert "The app found no student number, date of birth or national ID number." in res.text
    assert "removed-sample" not in res.text and "0 values" not in res.text


def test_the_review_names_the_removed_count_when_there_is_one(client: FormClient) -> None:
    res = client.post(
        "/courses/import", data={"pasted": "Student number: 512345\nFEB11001 Mathematics I 8,0\n"}
    )
    assert "1 value the app recognised" in res.text and "removed-sample" in res.text


def test_the_send_busy_notice_says_what_is_happening(client: FormClient) -> None:
    """W3: the button says "Sending…"; the notice and announcement no longer say "Reading the courses."."""
    res = client.post("/courses/import", data={"pasted": "FEB11001 Mathematics I 8,0\n"})
    assert "Sending the text to Claude Code and reading the courses." in res.text
    assert ">Reading the courses.<" not in res.text


# ---- data-integrity review round 4 (session 3, DECISIONS #78) ---

MODES = {"read": {}, "send": {"marker_fills_slot": True}}


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize(
    "text",
    [
        "Student number\tDate of birth: 12-03-2001\n598431\n",
        "Studentnummer\tGeboortedatum 12-03-2001\n598431\n",
    ],
)
def test_a_label_never_takes_the_next_cells_label_value(text: str, mode: str) -> None:
    """F1 HIGH: "Student number<TAB>Date of birth: 12-03-2001" gave the date to the student number, so its own
    value on the next row was never removed. A cell holding a label is not this label's value cell."""
    try:
        out = identifiers.strip(text, CFG.courses, **MODES[mode])
    except identifiers.UnresolvedIdentifier:
        return  # stopping is fail closed, also acceptable
    assert "598431" not in out and "12-03-2001" not in out, out


def test_a_label_whose_next_cell_is_a_label_and_no_row_follows_stops() -> None:
    with pytest.raises(identifiers.UnresolvedIdentifier):
        identifiers.strip("Student number\tDate of birth: 12-03-2001\n", CFG.courses)


def test_in_returned_fields_the_neighbouring_label_case_removes_both() -> None:
    out = identifiers.strip("Student number\tDate of birth: 12-03-2001\n598431\n", CFG.courses, strict=False)
    assert "598431" not in out and "12-03-2001" not in out, out


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize(
    "text",
    [
        "Student number\tName\t\n598431\tJan\t512345\n",  # trailing tab: a blank heading
        "Student number\tName\t\n598431\tJan\t12-03-2001\n",
        "Student number\tName\t \n598431\tJan\t512345\n",  # a whitespace-only heading
        "Student number\tName\t\t\n598431\tJan\t512345\t498765\n",
        "Student number\t\tName\n598431\t512345\tJan\n",  # a blank heading in the middle
    ],
)
def test_an_id_shaped_token_under_a_blank_heading_stops(text: str, mode: str) -> None:
    """F2 MEDIUM: #77 counted blank heading cells as headings."""
    with pytest.raises(identifiers.UnresolvedIdentifier):
        identifiers.strip(text, CFG.courses, **MODES[mode])


def test_in_send_mode_a_marker_under_a_blank_heading_stops() -> None:
    with pytest.raises(identifiers.UnresolvedIdentifier):
        identifiers.strip(
            "Student number\tName\t\n[removed]\tJan\t[removed]\n", CFG.courses, marker_fills_slot=True
        )


@pytest.mark.parametrize(
    ("text", "gone"),
    [
        ("Student number\tName\n598431\tJan\t512345\n", ["598431", "512345"]),
        ("Name\tStudent number\nJan\t598431\t512345\n", ["598431", "512345"]),
        ("\tStudent number\tName\n\t598431\tJan\t512345\n", ["598431", "512345"]),
        ("Student ID\n2019 512345\n", ["512345"]),
    ],
)
def test_in_returned_fields_an_unresolved_value_row_loses_every_candidate(text: str, gone: list[str]) -> None:
    """F3 LOW: without strict, a value row that can't be matched was left as it was; over-removal is the safe
    side (#74)."""
    out = identifiers.strip(text, CFG.courses, strict=False)
    for value in gone:
        assert value not in out, out


# ---- ui review round 4, N1 (session 3, DECISIONS #79) ---


def test_a_clean_check_again_says_every_course_can_be_accepted(client: FormClient, fake_rows) -> None:
    """N1: with nothing left to fix, Check again showed "0 courses still have a problem. First: …: ."."""
    rows = [dict(r) for r in EXPECTED["courses"]]
    good = rows[0]["grade"]
    rows[0]["grade"] = "10,0"
    fake_rows(_output(rows))
    fields = set_field(form_fields(_upload(client, "txt").text, "courses-preview"), "row-0-grade", good)
    html = client.post("/courses/import/save", data=[*fields, ("accept", "check")]).text
    assert "notice-problem" not in html and "still have a problem" not in html
    main = re.search(r'<div class="main-inner[^"]*"[^>]*>', html).group(0)
    assert _attr(main, "data-announce") == "Checked again: every course can be accepted."
    assert re.search(r'<p class="notice"[^>]*>Checked again: every course can be accepted\.</p>', html)
    # changed with disclosure (DECISIONS #85, user decision on ui round 5 W1): focus goes to the success
    # notice, so it shows with the first course (it was "row-0", #79, and the notice sat above the viewport)
    assert _attr(main, "data-focus") == "import-notice"
    assert re.search(r'<p class="notice" role="status" id="import-notice" tabindex="-1">', html)


def test_check_again_with_every_course_saved_says_so(client: FormClient, fake_rows) -> None:
    fields = form_fields(_upload(client, "txt").text, "courses-preview")
    saved = client.post("/courses/import/save", data=[*fields, ("accept", "all")]).text
    html = client.post(
        "/courses/import/save", data=[*form_fields(saved, "courses-preview"), ("accept", "check")]
    ).text
    assert "notice-problem" not in html and "can be accepted." not in html
    assert re.search(r'<p class="notice"[^>]*>Checked again: every course is saved\.</p>', html)


# ---- ai-output review round 2, F1 (session 3, DECISIONS #80) ----------------------------------------------


@pytest.mark.parametrize("fmt", list(FORMATS))
@pytest.mark.parametrize("grade", ["2021", "1", "2022"])  # the years and the block number of the row's term
def test_a_grade_equal_to_the_rows_term_is_flagged_even_without_a_returned_term(fmt: str, grade: str) -> None:
    """F1: on untabbed text (the PDF) the term was taken out of the row only when the model returned it, so a
    wrong grade "2021" or "1" was "found" in the term when the term came back null (docs/02 §10.2)."""
    text = identifiers.strip(_text(fmt), CFG.courses)
    row = {"name": "Mathematics I", "code": "FEB11001", "grade": grade, "credits": 4.0, "term": None}
    assert importer.GRADE_NOT_FOUND in importer.row_problems(row, text, [], CFG.courses), fmt


@pytest.mark.parametrize("fmt", list(FORMATS))
def test_the_real_grade_is_still_found_without_a_returned_term(fmt: str) -> None:
    text = identifiers.strip(_text(fmt), CFG.courses)
    first = EXPECTED["courses"][0]
    row = {**{k: first[k] for k in ("name", "code", "grade", "credits")}, "term": None}
    assert importer.GRADE_NOT_FOUND not in importer.row_problems(row, text, [], CFG.courses), fmt


@pytest.mark.parametrize(
    ("line", "grade", "found"),
    [
        ("C1 Course one 7 2023-2024 semester 2\n", "2", False),
        ("C1 Course one 7 Fall 2023\n", "2023", False),
        ("C1 Course one 7 periode 3\n", "3", False),
        ("C1 Course one 7 2023-2024 semester 2\n", "7", True),
    ],
)
def test_term_words_and_years_never_lend_a_grade(line: str, grade: str, found: bool) -> None:
    assert importer.grade_on_row(line, "Course one", "C1", grade, None, None, CFG.courses) is found


# ---- data-integrity review round 5 (session 3, DECISIONS #81) ---------------------------------------------

ROW = "FEB11010\tEconometrics I\t8,0\t4"


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize(
    "text",
    [
        f"Student number\t598431 Date of birth 12-03-2001\n{ROW}\n",
        f"Student number\t598431 (BSN 123456782)\n{ROW}\n",
        f"Student number\t598431, date of birth 12-03-2001\n{ROW}\n",
        "Student number\t598431 Date of birth 12-03-2001\nFEB11010 Econometrics I 8,0 4\n",
        f"Student number\tDate of birth\t598431\t12-03-2001\n{ROW}\n",
    ],
)
def test_a_label_row_never_keeps_an_identifier(text: str, mode: str) -> None:
    """F1 HIGH (my regression in #78): a label skipped as a "label row" left the value in its own or a later
    cell, and took the next row's course code instead."""
    try:
        out = identifiers.strip(text, CFG.courses, **MODES[mode])
    except identifiers.UnresolvedIdentifier:
        return
    for value in ("598431", "12-03-2001", "123456782"):
        assert value not in out, out
    assert "FEB11010" in out, out  # never the course code in place of the identifier


@pytest.mark.parametrize(
    "text",
    [
        f"Student number\t598431 Date of birth 12-03-2001\n{ROW}\n",
        f"Student number\tDate of birth\t598431\t12-03-2001\n{ROW}\n",
    ],
)
def test_in_returned_fields_a_label_row_loses_every_candidate(text: str) -> None:
    out = identifiers.strip(text, CFG.courses, strict=False)
    assert "598431" not in out and "12-03-2001" not in out, out


def test_a_label_row_paste_never_reaches_claude_or_sources(client: FormClient, calls: Path) -> None:
    res = client.post(
        "/courses/import",
        data={"pasted": f"Transcript\nStudent number\t598431 Date of birth 12-03-2001\n{ROW}\n"},
    )
    assert res.status_code == 409 or "598431" not in res.text
    assert not calls.exists()  # Read never calls the model


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        ("Student number\t5123-45\n", "5123"),
        ("Student number\t12/3 2001\n", "12/3"),
    ],
)
def test_in_returned_fields_a_key_value_cell_loses_every_candidate(text: str, kept: str) -> None:
    """F3 LOW: the cell was left unchanged without strict."""
    out = identifiers.strip(text, CFG.courses, strict=False)
    assert kept not in out, out


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize(
    "text",
    [
        "Student number\t-\n598431\t512345\n",  # a punctuation-only heading heads nothing
        "Student number\t:\n598431\t12-03-2001\n",
        "Student number\tDate of birth: 12-03-2001\n598431\t512345\n",  # under an identifier label's column
    ],
)
def test_an_id_under_a_non_heading_or_an_identifier_heading_stops(text: str, mode: str) -> None:
    """F4 LOW."""
    with pytest.raises(identifiers.UnresolvedIdentifier):
        identifiers.strip(text, CFG.courses, **MODES[mode])


@pytest.mark.parametrize(
    ("line", "grade"),
    [
        ("FEB11010 Econometrics I 1e semester 7,5 4\n", "7,5"),
        ("FEB11010 Econometrics I 2nd semester 8 4\n", "8"),
        ("FEB11010 Econometrics I Fall semester 8.5 4\n", "8.5"),
        ("FEB11010 Econometrics I Spring term 7 4\n", "7"),
        ("FEB11010 Econometrics I 2021 - 65 20\n", "65"),
        ("FEB11010 Econometrics I 2021 - 28 6\n", "28"),
    ],
)
def test_term_tokens_never_hide_a_real_grade(line: str, grade: str) -> None:
    """F2 MEDIUM (my regression in #80): a term word after an ordinal or season, or "YYYY - NN" that is no
    year range, took the grade."""
    assert importer.grade_on_row(line, "Econometrics I", "FEB11010", grade, None, None, CFG.courses)


@pytest.mark.parametrize(
    ("line", "name", "grade"),
    [
        ("FIN201 Long Term 8 6\n", "Long Term", "8"),
        ("FIN201 Research Period 9 6\n", "Research Period", "9"),
    ],
)
def test_a_term_word_in_the_course_name_never_hides_its_grade(line: str, name: str, grade: str) -> None:
    assert importer.grade_on_row(line, name, "FIN201", grade, None, None, CFG.courses)


@pytest.mark.parametrize(
    ("line", "name", "grade"),
    [
        ("FIN201 Mathematics 2 (Block 3) 7 6\n", "Mathematics 2", "2"),
        ("FIN201 Econometrics 2 (2021) 7 6\n", "Econometrics 2", "2"),
        ("FIN201 Econometrics Block 1A B 6\n", "Econometrics", "A"),
        ("FIN201 Econometrics 2021A B 6\n", "Econometrics", "A"),
    ],
)
def test_term_tokens_never_let_a_wrong_grade_through(line: str, name: str, grade: str) -> None:
    assert not importer.grade_on_row(line, name, "FIN201", grade, None, None, CFG.courses)


# ---- data-integrity review round 6 (session 3, DECISIONS #82) ---------------------------------------------

DOB = "12-03-2001"


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize(
    "text",
    [
        f"Student number Date of birth\tS2034567\t{DOB}\n{ROW}\n",
        f"Student number Date of birth\t123456789\t{DOB}\n{ROW}\n",
        f"Studentnummer Geboortedatum\t12345678\t{DOB}\n{ROW}\n",
        f"Student number Date of birth\tS2034567\t12 March 2001\n{ROW}\n",
        f"Student number Date of birth\t598431\t{DOB}\n{ROW}\n",
        f"Date of birth Student number\t{DOB}\t598431\n{ROW}\n",
    ],
)
def test_two_labels_in_one_cell_never_share_a_value(text: str, mode: str) -> None:
    """F1 HIGH (predates): both labels took the next cell; the second value was never checked and the
    same span was replaced twice ("[removed]ed]")."""
    try:
        out = identifiers.strip(text, CFG.courses, **MODES[mode])
    except identifiers.UnresolvedIdentifier:
        return
    for value in ("S2034567", "123456789", "12345678", DOB, "12 March 2001", "598431"):
        assert value not in out, out
    assert "ed]" not in out.replace("[removed]", ""), out  # never a mangled marker


def test_overlapping_spans_are_replaced_once() -> None:
    assert identifiers._replace("ab 123456 cd", [(3, 9), (3, 9), (5, 8)], "[removed]") == "ab [removed] cd"


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize(
    "text",
    [
        f"Student number\tProgramme: BSc Econometrics (50952)\n598431\n{ROW}\n",
        f"Student number\tProgramme code 50952\n598431\tBSc Econometrics\n{ROW}\n",
    ],
)
def test_a_value_cell_holds_only_the_value(text: str, mode: str) -> None:
    """F2 HIGH (predates): a next cell with words before the number gave that number (a programme code) to
    the label, and the real student number on the next row was sent."""
    try:
        out = identifiers.strip(text, CFG.courses, **MODES[mode])
    except identifiers.UnresolvedIdentifier:
        return
    assert "598431" not in out, out


@pytest.mark.parametrize(
    "pasted",
    [
        f"Transcript\nStudent number Date of birth\tS2034567\t{DOB}\n{ROW}\n",
        f"Transcript\nStudent number\tProgramme: BSc Econometrics (50952)\n598431\n{ROW}\n",
    ],
)
def test_round_6_pastes_never_reach_the_review_page(client: FormClient, calls: Path, pasted: str) -> None:
    res = client.post("/courses/import", data={"pasted": pasted})
    assert res.status_code == 409 or ("598431" not in res.text and DOB not in res.text), res.text[-600:]
    assert not calls.exists()


def _save_text(client: FormClient, fake_rows, pasted: str, row: dict) -> str:
    fake_rows({"courses": [row], "warnings": []})
    review = client.post("/courses/import", data={"pasted": pasted})
    assert review.status_code == 200, review.text[-400:]
    preview = _send(client, review.text)
    fields = form_fields(preview.text, "courses-preview")
    res = client.post("/courses/import/save", data=[*fields, ("accept", "0")])
    assert res.status_code == 200, res.text[-400:]
    return dict(form_fields(res.text, "courses-preview"))["text"]


def _course(code: str, name: str, grade: str, credits: float) -> dict:
    return {
        "name": name,
        "code": code,
        "institution": None,
        "grade": grade,
        "credits": credits,
        "term": None,
        "lecturers": [],
        "description": "",
    }


def test_save_keeps_the_course_code_after_a_removed_identifier(
    client: FormClient, data_dir: Path, fake_rows
) -> None:
    """F3 MEDIUM: the Save re-strip (not strict, no marker slot) took the next line's course code as the
    value of "Student number: [removed]", in sources/ and in the Check-again text."""
    text = _save_text(
        client,
        fake_rows,
        "Transcript\nStudent number: 598431\nFEB11010 Econometrics I 8,0 4\n",
        _course("FEB11010", "Econometrics I", "8,0", 4.0),
    )
    assert "FEB11010 Econometrics I" in text
    (source,) = (data_dir / "sources").glob("*.txt")
    stored = source.read_text(encoding="utf-8")
    assert "FEB11010 Econometrics I 8,0 4" in stored and "598431" not in stored


def test_save_keeps_a_programme_code_in_a_value_row(client: FormClient, data_dir: Path, fake_rows) -> None:
    text = _save_text(
        client,
        fake_rows,
        "Studentnummer\tOpleiding\n598431\tBSc Economie, code 50952\nFEB11010 Econometrics I 8,0 4\n",
        _course("FEB11010", "Econometrics I", "8,0", 4.0),
    )
    # input changed with the user's approval (#93): "(50952)" alone in brackets is now removed (rule 2)
    assert "code 50952" in text and "598431" not in text


def test_a_programme_code_alone_in_brackets_is_removed_like_any_bracketed_number() -> None:
    """Rule 2 (#93): five digits alone in brackets can't be told from a student number, so they go."""
    out = identifiers.strip("Studentnummer\tOpleiding\n598431\tBSc Economie (50952)\n", CFG.courses)
    assert "50952" not in out and "598431" not in out


def test_save_refuses_an_edited_text_that_no_longer_strips(
    client: FormClient, data_dir: Path, fake_rows
) -> None:
    fake_rows({"courses": [_course("FEB11010", "Econometrics I", "8,0", 4.0)], "warnings": []})
    review = client.post("/courses/import", data={"pasted": "FEB11010 Econometrics I 8,0 4\n"})
    fields = form_fields(_send(client, review.text).text, "courses-preview")
    fields = set_field(fields, "text", "Student number\n[removed] 512345\nFEB11010 Econometrics I 8,0 4\n")
    before = data_snapshot(data_dir)
    res = client.post("/courses/import/save", data=[*fields, ("accept", "0")])
    assert res.status_code == 409  # the hidden text was edited: refused, never over-removed
    assert "512345" not in res.text
    assert data_snapshot(data_dir) == before


@pytest.mark.parametrize(
    ("line", "grade"),
    [
        ("FEB11010 Econometrics I Fall semester 2 7 4\n", "2"),
        ("FEB11010 Econometrics I first period 1 7 4\n", "1"),
        ("FEB11010 Econometrics I 2e blok 3 7 4\n", "3"),
        ("FEB11010 Econometrics I Spring term 2 7 4\n", "2"),
        ("FEB11010 Econometrics I Q 1 7 4\n", "1"),
        ("FEB11010 Econometrics I S 1 7 4\n", "1"),
        ("FEB11010 Econometrics I Sem. 2 8 4\n", "2"),
    ],
)
def test_a_term_number_is_never_a_grade(line: str, grade: str) -> None:
    """F4 LOW (qualifier + term word + term number) and F5 LOW (sem, q, s)."""
    assert not importer.grade_on_row(line, "Econometrics I", "FEB11010", grade, 4.0, None, CFG.courses)


@pytest.mark.parametrize(
    ("line", "grade"),
    [
        ("FEB11010 Econometrics I Fall semester 2 7 4\n", "7"),
        ("FEB11010 Econometrics I 2nd semester 8 4\n", "8"),
        ("FEB11010 Econometrics I Sem. 2 8 4\n", "8"),
        ("FEB11010 Econometrics I Q 1 7 4\n", "7"),
    ],
)
def test_the_grade_after_a_term_number_is_found(line: str, grade: str) -> None:
    assert importer.grade_on_row(line, "Econometrics I", "FEB11010", grade, 4.0, None, CFG.courses)


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize(
    "text",
    [
        f"Student number\tDate of birth 12-03-2001\n{ROW}\n",
        f"Student number\tBorn 2001\n{ROW}\n",
    ],
)
def test_a_course_row_is_never_a_label_rows_value_row(text: str, mode: str) -> None:
    """F6 LOW (new in #81): the course code was taken as the student number."""
    with pytest.raises(identifiers.UnresolvedIdentifier):
        identifiers.strip(text, CFG.courses, **MODES[mode])


# ---- data-integrity review round 7 (session 3, DECISIONS #83) ---------------------------------------------


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize(
    "text",
    [
        f"Student number Date of birth\tProgramme\nS2034567\t{DOB}\n{ROW}\n",
        f"Student number Date of birth\tProgramme\n598431\t{DOB}\tBSc\n{ROW}\n",
        f"Date of birth Student number\tProgramme\n{DOB}\t598431\n{ROW}\n",
        f"Studentnummer Geboortedatum\tOpleiding\n598431\t{DOB}\n{ROW}\n",
        f"Naam\tStudentnummer Geboortedatum\tOpleiding\nNoor\t598431\t{DOB}\tBSc\n{ROW}\n",
        f"Student number / Date of birth\tProgramme\n598431\t{DOB}\n{ROW}\n",
        f"Student number Date of birth\tProgramme\tFaculty\n598431\t{DOB}\tEcon\n{ROW}\n",
    ],
)
def test_two_labels_in_one_label_row_cell_never_share_a_column(text: str, mode: str) -> None:
    """F1 HIGH (predates, #67): both labels took the same column and the second value (a merged Word header
    cell "Student number / Date of birth") went to Claude and sources/."""
    with pytest.raises(identifiers.UnresolvedIdentifier):  # Read and Send stop; fields: the next test
        identifiers.strip(text, CFG.courses, **MODES[mode])


@pytest.mark.parametrize("text", [f"Student number Date of birth\tProgramme\n598431\t{DOB}\tBSc\n{ROW}\n"])
def test_in_returned_fields_a_shared_column_row_loses_every_candidate(text: str) -> None:
    out = identifiers.strip(text, CFG.courses, strict=False)
    assert "598431" not in out and DOB not in out, out


def test_a_merged_docx_header_cell_stops_the_import() -> None:
    from docx import Document

    doc = Document()
    table = doc.add_table(rows=2, cols=3)
    head = table.cell(0, 0).merge(table.cell(0, 1))
    head.text = "Student number / Date of birth"
    table.cell(0, 2).text = "Programme"
    for j, value in enumerate(["598431", DOB, "BSc Econometrics"]):
        table.cell(1, j).text = value
    buffer = __import__("io").BytesIO()
    doc.save(buffer)
    text = extract.extract_text("t.docx", buffer.getvalue(), CFG.courses)
    with pytest.raises(identifiers.UnresolvedIdentifier):
        identifiers.strip(text, CFG.courses)


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize(
    ("text", "parts"),
    [
        ("Date of birth\t12\t03\t2001\n", ["03", "2001"]),
        ("Date of birth\t12\tMarch\t2001\n", ["March", "2001"]),
        ("Date of birth\tProgramme\n12\t03\t2001\tBSc\n", ["03", "2001"]),
        ("Student number\t598\t431\n", ["431"]),
    ],
)
def test_a_value_split_across_cells_is_never_taken_in_part(text: str, parts: list[str], mode: str) -> None:
    """F2 MEDIUM (predates): only the first cell of "12<TAB>03<TAB>2001" was removed."""
    if mode == "fields":
        out = identifiers.strip(text, CFG.courses, strict=False)
        assert not all(part in out for part in parts), out
        return
    with pytest.raises(identifiers.UnresolvedIdentifier):
        identifiers.strip(text, CFG.courses, **MODES[mode])


@pytest.mark.parametrize(
    ("line", "grade"),
    [
        ("FEB11010 Econometrics I Master's 9 4\n", "9"),
        ("FEB11010 Econometrics I Women's 8 4\n", "8"),
        ("FEB11010 Econometrics I 2023 S 8 4\n", "8"),
        ("FEB11010 Econometrics I 2022 Q 8 4\n", "8"),
        ("FEB11010\tEconometrics I\tS\t8\t4\n", "8"),
        ("FEB11010 Econometrics I Fall semester 8 4 12-01-2022\n", "8"),
        ("FEB11010 Econometrics I Fall semester 8 4 A\n", "8"),
        ("FEB11010 Econometrics I Fall semester 8 4 2\n", "8"),
    ],
)
def test_round_7_real_grades_are_found(line: str, grade: str) -> None:
    """F3 (q and s as term words) and F4 (values after the credits counted): regressions of #82. With no
    credits returned, "Fall semester 8 4 A" can't be told from "Fall semester 2 7 4" (the documented
    credits-null limit, 02 §10.2), so those two rows are checked with credits only (#83, disclosed)."""
    trailing = line.rstrip().endswith((" A", " 2"))
    for credits in (4.0,) if trailing else (4.0, None):
        assert importer.grade_on_row(line, "Econometrics I", "FEB11010", grade, credits, None, CFG.courses), (
            line,
            credits,
        )


@pytest.mark.parametrize(
    "text",
    [
        "Student number\t598431 (full-time)\n",
        "Date of birth\t12-03-2001 (Rotterdam)\n",
        "Student number\tS2034567 BSc\n",
    ],
)
def test_words_after_a_key_value_cells_value_still_import(text: str) -> None:
    """F6 (#82 over-stop): words before the number make it no value cell (r6 F2); words after it don't."""
    out = identifiers.strip(text, CFG.courses)
    assert "598431" not in out and "12-03-2001" not in out and "S2034567" not in out


def test_save_says_what_happened_when_the_text_changed(client: FormClient, data_dir: Path, fake_rows) -> None:
    """F7: the Save refusal used the Read message ("Nothing was sent to Claude. Remove that part of the
    document")."""
    fake_rows({"courses": [_course("FEB11010", "Econometrics I", "8,0", 4.0)], "warnings": []})
    review = client.post("/courses/import", data={"pasted": "FEB11010 Econometrics I 8,0 4\n"})
    fields = form_fields(_send(client, review.text).text, "courses-preview")
    fields = set_field(fields, "text", "Student number\n[removed] 512345\nFEB11010 Econometrics I 8,0 4\n")
    res = client.post("/courses/import/save", data=[*fields, ("accept", "0")])
    assert res.status_code == 409
    assert "Nothing was saved: the text changed after it was checked." in res.text
    assert "Nothing was sent to Claude" not in res.text


# ---- known layouts only (user decision, session 3, DECISIONS #84) ---

LAYOUT_STRIPS = [
    ("Student number: 512345\n", ["512345"]),  # 1: "Label: value"
    (
        "Diploma register 2023-004512 - Document number 123456789\n",
        ["2023-004512", "123456789"],
    ),  # 1, two pairs
    ("Student ID\nS2034567\n", ["S2034567"]),  # 2: the label, its value on the next line
    (
        "Studentnummer Geboortedatum Opleiding\n598431 12-03-2001 BSc\n",
        ["598431", "12-03-2001"],
    ),  # 2, PDF row
    ("Student number\t512345\tProgramme\tBSc\n", ["512345"]),  # 3: label cell, value cell
    ("Studentnummer\tGeboortedatum\tOpleiding\n598431\t12-03-2001\tBSc\n", ["598431", "12-03-2001"]),  # 4
    ("Name: Noor\tStudent number: 598431\n", ["598431"]),  # 1 in a cell
]


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize(("text", "gone"), LAYOUT_STRIPS)
def test_the_four_layouts_strip(text: str, gone: list[str], mode: str) -> None:
    first = identifiers.strip(text, CFG.courses)
    out = first if mode == "read" else identifiers.strip(first, CFG.courses, marker_fills_slot=True)
    assert out == first or mode == "read"  # Send changes nothing in stripped text
    for value in gone:
        assert value not in out, out


LAYOUT_STOPS = [
    "Student number Programme 598431\n",  # words between the label and a number
    "Student number: 512345 Date of birth\n12-03-2001\n",  # one label with its value, one without
    "Student ID\n\tS2034567\n",  # a label line, then a table row
    "Studentnummer\tGeboortedatum\n598431 12-03-2001\n",  # a heading row, then a line without tabs
    "Student number\tDate of birth: 12-03-2001\n598431\n",  # a heading cell beside a "Label: value" cell
    "Student number\nDate of birth\n598431\n",  # a label line, then another label line
    "Ref 598431 Student number: 512345\n",  # an ID-shaped number before the label
]


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize("text", LAYOUT_STOPS)
def test_any_other_layout_stops_with_what_to_do(text: str, mode: str) -> None:
    with pytest.raises(identifiers.UnresolvedIdentifier) as err:
        identifiers.strip(text, CFG.courses, **MODES[mode])
    message = str(err.value)
    assert "Nothing was sent to Claude" in message and 'put the identifier as "Label: value"' in message


@pytest.mark.parametrize("text", LAYOUT_STOPS)
def test_in_returned_fields_another_layout_loses_every_candidate(text: str) -> None:
    out = identifiers.strip(text, CFG.courses, strict=False)
    for value in ("598431", "512345", "12-03-2001", "S2034567"):
        assert value not in out, out


@pytest.mark.parametrize("fmt", list(FORMATS))
def test_the_fixture_uses_only_the_four_layouts(fmt: str) -> None:
    """The fixture is unchanged (user decision): every layout in it is one of the four."""
    stripped = identifiers.strip(_text(fmt), CFG.courses)
    _assert_no_identifier(stripped, fmt)


# ---- ai-output review round 2, F2: copied as written (user decision, session 3, DECISIONS #86) -----------


def _fixture_row(n: int = 0, **changes: object) -> dict:
    return {**EXPECTED["courses"][n], "description": "", **changes}


@pytest.mark.parametrize("fmt", list(FORMATS))
@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"description": "An advanced course taught with real cases."}, importer.DESCRIPTION_NOT_FOUND),
        ({"institution": "Harvard"}, importer.INSTITUTION_NOT_FOUND),
        ({"lecturers": ["Prof. dr. A. van den Berg", "Prof. Invented Person"]}, importer.LECTURER_NOT_FOUND),
        ({"lecturers": ["A. van den Berg", "Dr. J. Smit"]}, importer.LECTURER_NOT_FOUND),  # title dropped
    ],
)
def test_text_not_in_the_document_is_flagged(fmt: str, changes: dict, problem: str) -> None:
    """Rule 15: description, institution and lecturers must stand in the document as written (only
    whitespace and line breaks normalised); a transcript without a description gives ""."""
    text = identifiers.strip(_text(fmt), CFG.courses)
    assert problem in importer.kinds(importer.row_problems(_fixture_row(**changes), text, [], CFG.courses))


@pytest.mark.parametrize("fmt", list(FORMATS))
def test_every_fixture_row_as_written_has_no_problem(fmt: str) -> None:
    text = identifiers.strip(_text(fmt), CFG.courses)
    for n in range(len(EXPECTED["courses"])):
        assert importer.row_problems(_fixture_row(n), text, [], CFG.courses) == [], EXPECTED["courses"][n]


def test_a_description_is_matched_with_whitespace_normalised() -> None:
    text = "FEB1\tCourse one\t8,0\t4\nDescription: Linear models,\n  estimation   and testing.\n"
    row = {
        "name": "Course one",
        "code": "FEB1",
        "grade": "8,0",
        "credits": 4.0,
        "term": None,
        "institution": None,
        "lecturers": [],
        "description": "Linear models, estimation and testing.",
    }
    assert importer.DESCRIPTION_NOT_FOUND not in importer.row_problems(row, text, [], CFG.courses)
    row["description"] = "Linear models, Estimation and testing."  # a changed capital is not as written
    assert importer.DESCRIPTION_NOT_FOUND in importer.row_problems(row, text, [], CFG.courses)


def test_a_flagged_description_is_marked_on_its_field(client: FormClient, fake_rows) -> None:
    rows = [dict(r, description="") for r in EXPECTED["courses"]]
    rows[3]["description"] = "Invented by the model."
    fake_rows({"courses": rows, "warnings": []})
    html = _upload(client, "txt").text
    area = re.search(r'<textarea id="row-3-description"[^>]*>', html).group(0)
    assert 'aria-invalid="true"' in area and "aria-describedby=" in area
    assert importer.DESCRIPTION_NOT_FOUND in html


# ---- data-integrity review round 8 (session 3, DECISIONS #87) ----------------------------------------------

R8_SECOND_ROW = [
    "Studentnummer\tGeboortedatum\tOpleiding\n598431\t12-03-2001\tBSc\n498765\t12-03-2001\tMSc\n",
    "Studentnummer\tGeboortedatum\tOpleiding\n598431\t12-03-2001\tBSc\n\n498765\t12-03-2001\tMSc\n",
    "Diploma\tDiploma register number\tYear\nVWO\t2017-001234\t2017\nBSc\t2023-004512\t2023\n",
    "Studentnummer Geboortedatum Opleiding\n598431 12-03-2001 BSc\n498765 12-03-2001 MSc\n",
    "Student number\tName\n512345\tNoor\n498765\tNoor (old)\n",
    "Student number\n512345\n498765\n",
]


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize("text", R8_SECOND_ROW)
def test_a_second_value_row_stops(text: str, mode: str) -> None:
    """F1 HIGH (inside layouts 4 and 2): rows after the value row were never checked."""
    with pytest.raises(identifiers.UnresolvedIdentifier):
        identifiers.strip(text, CFG.courses, **MODES[mode])


@pytest.mark.parametrize("text", R8_SECOND_ROW)
def test_in_returned_fields_a_second_value_row_loses_its_values(text: str) -> None:
    out = identifiers.strip(text, CFG.courses, strict=False)
    for value in ("498765", "2023-004512"):
        assert value not in out, out


@pytest.mark.parametrize(
    "text",
    [
        "Student number\tProgramme\n598431\tBSc\nCode\tCourse\tGrade\tCredits\n"
        "FEB11010\tEconometrics I\t8,0\t4\n",
        "Student ID\nS2034567\nStudentnummer\tOpleiding\n598431\tBSc\n",
        "Studentnummer\tOpleiding\n598431\tBSc\nProgramme: BSc Economics (programme code 50952)\n",
    ],
)
def test_what_follows_a_value_row_still_imports(text: str) -> None:
    out = identifiers.strip(text, CFG.courses)
    assert "598431" not in out and "S2034567" not in out


R8_BESIDE = [
    "Date of birth\t12-03-2001\t598431\n",
    "Student number\t512345\t498765\n",
    "Student number\t512345\t\t498765\n",
    "512345\tStudent number\t598431\n",
    "Student number: 598431\t12-03-2001\n",
    "Name: Noor\tStudent number: 598431\t512345\n",
]


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize("text", R8_BESIDE)
def test_an_id_beside_a_resolved_pair_stops(text: str, mode: str) -> None:
    """F2 MEDIUM (inside layouts 3 and 1 in a cell)."""
    with pytest.raises(identifiers.UnresolvedIdentifier):
        identifiers.strip(text, CFG.courses, **MODES[mode])


@pytest.mark.parametrize("text", R8_BESIDE)
def test_in_returned_fields_an_id_beside_a_pair_goes(text: str) -> None:
    out = identifiers.strip(text, CFG.courses, strict=False)
    for value in ("598431", "498765", "512345", "12-03-2001"):
        assert value not in out, out


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        ("Ref\t598431\tStudent number\t512345\n", "598431"),  # the value cell of a non-label key cell
        ("Student number\tS2034567\tBSc\t56966\n", "56966"),
        ("Programme code\t50952\tStudent number\t512345\n", "50952"),
    ],
)
def test_other_key_value_pairs_beside_a_label_stay(text: str, kept: str) -> None:
    out = identifiers.strip(text, CFG.courses)
    assert kept in out and "512345" not in out and "S2034567" not in out


@pytest.mark.parametrize(
    ("text", "gone"),
    [
        ("Student number (512345)\nFEB11010 Econometrics I 8,0 4\n", "512345"),
        ("BSN (123456782)\nFEB11010 Econometrics I 8,0 4\n", "123456782"),
        ("Date of birth (12-03-2001)\nFEB11010 Econometrics I 8,0 4\n", "12-03-2001"),
        ("Student number (512345)\tProgramme\tBSc\n", "512345"),
        ("Student number (512345) Date of birth (12-03-2001)\n", "512345"),
    ],
)
def test_a_bracketed_value_is_the_same_at_send(text: str, gone: str) -> None:
    """F3 MEDIUM (inside layout 1): "([removed])" was read as part of the label at Send."""
    first = identifiers.strip(text, CFG.courses)
    assert gone not in first
    assert identifiers.strip(first, CFG.courses, marker_fills_slot=True) == first
    assert "FEB11010" in first or "FEB11010" not in text


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize(
    "text", ["4521 Student number\nFEB11010 Econometrics I 8,0 4\n", "S2034 Student ID\nX1\n"]
)
def test_a_short_value_before_a_lone_label_stops(text: str, mode: str) -> None:
    """F6 LOW (counts as inside): layout 2's label line has no value on it."""
    with pytest.raises(identifiers.UnresolvedIdentifier):
        identifiers.strip(text, CFG.courses, **MODES[mode])


def test_a_month_name_date_after_a_value_is_its_own_value() -> None:
    """F8 LOW: "598431 12" was grouped as one number before "maart 2001"."""
    out = identifiers.strip("Studentnummer Geboortedatum\n598431 12 maart 2001\n", CFG.courses)
    assert out == "Studentnummer Geboortedatum\n[removed] [removed]\n"


@pytest.mark.parametrize(
    "text",
    [
        "FEB11010\tEconometrics I\t8,0\t4\nLecturer: Prof. dr. A. van den Berg\n"
        "FEB11020\tStatistics\t7,0\t4\n",
        "FEB11010 Econometrics I 8,0 4 Prof. dr. A. van\nden Berg\nFEB11020 Statistics 7,0 4\n",
    ],
)
def test_a_lecturer_on_the_courses_continuation_line_is_found(text: str) -> None:
    """F4 MEDIUM (#86): a lecturer printed on the line after the course's own line was flagged."""
    row = {
        "name": "Econometrics I",
        "code": "FEB11010",
        "grade": "8,0",
        "credits": 4.0,
        "term": None,
        "institution": None,
        "lecturers": ["Prof. dr. A. van den Berg"],
        "description": "",
    }
    assert importer.LECTURER_NOT_FOUND not in importer.kinds(
        importer.row_problems(row, text, [], CFG.courses)
    )
    other = dict(row, name="Statistics", code="FEB11020", grade="7,0")  # not this course's lecturer
    assert importer.LECTURER_NOT_FOUND in importer.kinds(importer.row_problems(other, text, [], CFG.courses))


@pytest.mark.parametrize(
    ("field", "value", "problem"),
    [
        ("lecturers", ["Dr. J. Smi"], importer.LECTURER_NOT_FOUND),
        ("lecturers", ["Berg"], importer.LECTURER_NOT_FOUND),
        ("lecturers", ["van den Berg"], importer.LECTURER_NOT_FOUND),
        # (an institution fragment such as "Example" stays open: it needs section-aware matching; #87)
        ("description", "a", importer.DESCRIPTION_NOT_FOUND),
    ],
)
def test_fragments_are_not_as_written(field: str, value: object, problem: str) -> None:
    """F5 LOW (#86): matches are word-bounded and a name must not be the tail of a longer name."""
    text = identifiers.strip(_text("txt"), CFG.courses)
    assert problem in importer.kinds(
        importer.row_problems(_fixture_row(**{field: value}), text, [], CFG.courses)
    )


def test_a_glued_title_counts_as_a_title() -> None:
    text = "FEB11010\tEconometrics I\t8,0\t4\tProf.dr. A. van den Berg\n"
    row = {
        "name": "Econometrics I",
        "code": "FEB11010",
        "grade": "8,0",
        "credits": 4.0,
        "term": None,
        "institution": None,
        "lecturers": ["A. van den Berg"],
        "description": "",
    }
    assert importer.LECTURER_NOT_FOUND in importer.kinds(importer.row_problems(row, text, [], CFG.courses))


# ---- session 4: the user's answers to the session-3 open questions ----------------------------------------


def _docx_bytes(build) -> bytes:
    from docx import Document

    doc = Document()
    build(doc)
    buffer = __import__("io").BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def test_a_soft_line_break_inside_a_table_cell_reads_as_a_space() -> None:
    """Q1 (r8 F7): a .docx soft line break in a cell ("Student⏎number") split the label over two lines, so
    the label was not seen and its value was sent. Inside a cell a soft break now reads as a space."""

    def build(doc) -> None:
        table = doc.add_table(rows=1, cols=2)
        run = table.cell(0, 0).paragraphs[0].add_run("Student")
        run.add_break()
        table.cell(0, 0).paragraphs[0].add_run("number")
        table.cell(0, 1).text = "598431"

    text = extract.extract_text("t.docx", _docx_bytes(build), CFG.courses)
    assert text.splitlines()[0] == "Student number\t598431", text
    out = identifiers.strip(text, CFG.courses)
    assert "598431" not in out and CFG.courses.removed_marker in out, out


def test_a_soft_line_break_in_a_paragraph_still_starts_a_new_line() -> None:
    """Control: outside a table a soft break stays a line break (layout 2 reads the next line)."""

    def build(doc) -> None:
        run = doc.add_paragraph().add_run("Student ID")
        run.add_break()
        doc.paragraphs[0].add_run("S2034567")

    text = extract.extract_text("t.docx", _docx_bytes(build), CFG.courses)
    assert text.splitlines()[:2] == ["Student ID", "S2034567"], text


TABS_AFTER_COLON = [
    ("Student number:\t\t598431\n", "598431"),
    ("Date of birth:\t\t\t12-03-2001\n", "12-03-2001"),
    ("Name:\tNoor Example\nStudent number:\t\t598431\n", "598431"),
    ("Student number:\t\t598431\tProgramme:\t\tBSc Econometrics\n", "598431"),
]


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize(("text", "gone"), TABS_AFTER_COLON)
def test_tabs_after_label_colon_are_the_label_value_layout(text: str, gone: str, mode: str) -> None:
    """Q5 (user decision): Word tab alignment "Student number:<TAB><TAB>598431" is layout 1 and strips."""
    first = identifiers.strip(text, CFG.courses)
    out = first if mode == "read" else identifiers.strip(first, CFG.courses, marker_fills_slot=True)
    assert gone not in out and CFG.courses.removed_marker in out, out
    assert out == first


def test_tabs_after_label_colon_strip_in_a_docx_paragraph() -> None:
    def build(doc) -> None:
        p = doc.add_paragraph()
        p.add_run("Student number:")
        p.add_run().add_tab()
        p.add_run().add_tab()
        p.add_run("598431")

    text = extract.extract_text("t.docx", _docx_bytes(build), CFG.courses)
    out = identifiers.strip(text, CFG.courses)
    assert "598431" not in out, out


def test_tabs_after_label_colon_in_returned_fields_strip() -> None:
    out = identifiers.strip("Student number:\t\t598431\n", CFG.courses, strict=False)
    assert "598431" not in out, out


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize(
    "text",
    [
        "Student number\t\t598431\n",  # no colon: not "Label: value" (still outside)
        "Student number:\t\t598431\t512345\n",  # a second ID-shaped cell beside it
        "Student number:\t\t598\t431\n",  # a value split across cells
        "Student number:\t\tProgramme 598431\n",  # words before the number
    ],
)
def test_tabs_after_label_colon_still_stop_outside_the_layout(text: str, mode: str) -> None:
    with pytest.raises(identifiers.UnresolvedIdentifier):
        identifiers.strip(text, CFG.courses, **MODES[mode])


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize(
    ("text", "gone"),
    [
        ("Document nr. 512345\n", "512345"),
        ("Document no. 512345\n", "512345"),
        ("Document nummer: 512345\n", "512345"),
        ("Passport no. NX1234567\n", "NX1234567"),
        ("ID card number: IX9876543\n", "IX9876543"),
        ("Matrikelnummer: 1234567\n", "1234567"),
        ("Born: 12-03-2001\n", "12-03-2001"),
        ("Born:\t12-03-2001\n", "12-03-2001"),
        ("Born on 12 March 2001\n", "12 March 2001"),
        ("Born in 2001\n", "2001"),
        ("Born 12 March 2001\n", "12 March 2001"),  # directly before a date (user decision)
        ("Geboren: 12-03-2001\n", "12-03-2001"),
        ("Geboren op 12 maart 2001\n", "12 maart 2001"),
        ("Geboren in 2001\n", "2001"),
        ("geboren 12-03-2001\n", "12-03-2001"),
        ("Born 2001-03-12\n", "2001-03-12"),
    ],
)
def test_session_4_identifier_labels_strip(text: str, gone: str, mode: str) -> None:
    """Q6 (user decision): document nr./no./nummer, passport no., ID card number, matrikelnummer; "born" and
    "geboren" only as "born on/in", "geboren op/in", before a colon, or directly before a date."""
    first = identifiers.strip(text, CFG.courses)
    out = first if mode == "read" else identifiers.strip(first, CFG.courses, marker_fills_slot=True)
    assert gone not in out and CFG.courses.removed_marker in out, out


@pytest.mark.parametrize(
    "text",
    [
        "PHY 2010\tBorn Rule Seminar\t8\t2\tProf. dr. M. Born\n",  # a lecturer named Born, a course name
        "PHY 2010 Born Rule Seminar 8 2 Prof. dr. M. Born\n",
        "Description: covers the 1926 Born rule and its uses.\n",
        "Lecturer: Dr. K. Geboren\n",
    ],
)
def test_a_name_born_is_no_identifier_label(text: str) -> None:
    """Q6 (r2 F8): the word "born" alone, as a surname or in a course name, no longer stops the import or
    loses text."""
    assert identifiers.strip(text, CFG.courses) == text


# ---- ai-output review round 3 (session 4): lecturers and descriptions stay on the course's own lines ----

N1_LAYOUTS = {
    "code first, tabbed": ("FEB11001\tMathematics I\t8,0\t4", "FEB11002\tStatistics\t7,0\t4"),
    "term first, tabbed": (
        "2021-2022 block 1\tFEB11001\tMathematics I\t8,0\t4",
        "2021-2022 block 1\tFEB11002\tStatistics\t7,0\t4",
    ),
    "name first, untabbed": ("Mathematics I FEB11001 8,0 4", "Statistics FEB11002 7,0 4"),
    "numbered": ("1 FEB11001 Mathematics I 8,0 4", "2 FEB11002 Statistics 7,0 4"),
    "MIT-style code": ("15.001 Mathematics I 8,0 4", "15.002 Statistics 7,0 4"),
    "dashed code": ("FEB-11001 Mathematics I 8,0 4", "FEB-11002 Statistics 7,0 4"),
    "no code": ("Mathematics I 8,0 4", "Statistics 7,0 4"),
}
N1_CODES = {
    "MIT-style code": ("15.001", "15.002"),
    "dashed code": ("FEB-11001", "FEB-11002"),
    "no code": (None, None),
}
OWN, OTHER = "Prof. dr. A. van den Berg", "Dr. J. Smit"


def _n1_rows(layout: str) -> tuple[dict, dict]:
    first, second = N1_CODES.get(layout, ("FEB11001", "FEB11002"))
    base = {"term": None, "institution": None, "description": ""}
    maths = {
        **base,
        "name": "Mathematics I",
        "code": first,
        "grade": "8,0",
        "credits": 4.0,
        "lecturers": [OWN],
    }
    stats = {
        **base,
        "name": "Statistics",
        "code": second,
        "grade": "7,0",
        "credits": 4.0,
        "lecturers": [OTHER],
    }
    return maths, stats


@pytest.mark.parametrize("on_row", [False, True], ids=["Lecturer line", "on the row"])
@pytest.mark.parametrize("layout", list(N1_LAYOUTS))
def test_a_lecturer_of_the_next_course_is_flagged_in_every_layout(layout: str, on_row: bool) -> None:
    """N1 MEDIUM (my regression in #87 F4): a continuation ran to the next blank line unless a line started
    with a code, so the next course's lecturer was accepted in 6 of 7 layouts. The course's own lecturer is
    found; the next course's lecturer is flagged."""
    first, second = N1_LAYOUTS[layout]
    sep = "\t" if "\t" in first else " "
    if on_row:
        text = f"{first}{sep}{OWN}\n{second}{sep}{OTHER}\n"
    else:
        text = f"{first}\nLecturer: {OWN}\n{second}\nLecturer: {OTHER}\n"
    maths, stats = _n1_rows(layout)
    rows = [maths, stats]
    assert importer.LECTURER_NOT_FOUND not in importer.kinds(
        importer.row_problems(maths, text, [], CFG.courses, rows)
    )
    assert importer.LECTURER_NOT_FOUND not in importer.kinds(
        importer.row_problems(stats, text, [], CFG.courses, rows)
    )
    wrong = dict(maths, lecturers=[OTHER])
    assert importer.LECTURER_NOT_FOUND in importer.kinds(
        importer.row_problems(wrong, text, [], CFG.courses, rows)
    )


DESCRIBED = {
    "under each row": (
        "FEB11001\tMathematics I\t8,0\t4\nDescription: Linear algebra and calculus.\n"
        "FEB12003\tCorporate Finance\t7,5\t6\nDescription: Capital structure, payout policy and valuation.\n"
    ),
    "in an appendix": (
        "FEB11001\tMathematics I\t8,0\t4\nFEB12003\tCorporate Finance\t7,5\t6\n\nCourse descriptions\n"
        "FEB11001 Mathematics I\nLinear algebra and calculus.\n\n"
        "FEB12003 Corporate Finance\nCapital structure, payout policy and valuation.\n"
    ),
}


@pytest.mark.parametrize("layout", list(DESCRIBED))
def test_another_courses_description_is_flagged(layout: str) -> None:
    """N2 LOW: the description was checked against the whole document, so another course's description
    passed. It must stand on the course's own lines or their continuations."""
    text = DESCRIBED[layout]
    base = {"term": None, "institution": None, "lecturers": []}
    maths = {**base, "name": "Mathematics I", "code": "FEB11001", "grade": "8,0", "credits": 4.0}
    finance = {**base, "name": "Corporate Finance", "code": "FEB12003", "grade": "7,5", "credits": 6.0}
    rows = [maths, finance]
    own = dict(maths, description="Linear algebra and calculus.")
    other = dict(maths, description="Capital structure, payout policy and valuation.")
    assert importer.DESCRIPTION_NOT_FOUND not in importer.row_problems(own, text, [], CFG.courses, rows)
    assert importer.DESCRIPTION_NOT_FOUND in importer.row_problems(other, text, [], CFG.courses, rows)


def test_the_preview_checks_each_lecturer_against_the_other_returned_rows(
    client: FormClient, fake_rows
) -> None:
    """N1 at the route: the preview passes the other returned rows, so the next course's lecturer (rows
    numbered "1", "2", which the code-shape rule alone doesn't catch) blocks the row."""
    pasted = (
        "1 15.001 Mathematics I 8,0 4\nLecturer: Prof. dr. A. van den Berg\n"
        "2 15.002 Statistics 7,0 4\nLecturer: Dr. J. Smit\n"
    )
    maths = dict(_course("15.001", "Mathematics I", "8,0", 4.0), lecturers=["Dr. J. Smit"])
    stats = dict(_course("15.002", "Statistics", "7,0", 4.0), lecturers=["Dr. J. Smit"])
    fake_rows({"courses": [maths, stats], "warnings": []})
    review = client.post("/courses/import", data={"pasted": pasted})
    assert review.status_code == 200, review.text[-400:]
    html = _send(client, review.text).text
    assert re.search(r'<textarea id="row-0-lecturers"[^>]*aria-invalid="true"', html), html[-1500:]
    assert not re.search(r'<textarea id="row-1-lecturers"[^>]*aria-invalid="true"', html)


# ---- data-integrity review round 9 (session 4) -------------------------------------------------------------

R9_BORN = [
    f"Born\t12-03-2001\n{ROW}\n",
    f"Geboren\t12-03-2001\n{ROW}\n",
    f"born\t12-03-2001\n{ROW}\n",
    f"Name\tBorn\nNoor Example\t12-03-2001\n{ROW}\n",
    f"Born\n12-03-2001\n{ROW}\n",
    f"Geboren\n12 maart 2001\n{ROW}\n",
    f"Born - 12 March 2001\n{ROW}\n",
    f"Born, 12-03-2001\n{ROW}\n",
    f"Born  -  12-03-2001\n{ROW}\n",
    f"Born (12-03-2001)\n{ROW}\n",
    f"Born\t\t12-03-2001\n{ROW}\n",
    f"Born 12/3 2001\n{ROW}\n",
    f"Geboren te Rotterdam op 12-03-2001\n{ROW}\n",
]
R9_DATES = ("12-03-2001", "12 March 2001", "12 maart 2001", "12/3 2001")


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize("text", R9_BORN)
def test_a_born_label_in_the_four_layouts_never_lets_the_date_through(text: str, mode: str) -> None:
    """r9 F1 HIGH (my regression in #89): "Born" as a heading, label cell, label line or before layout 1's
    separators was no label, so the date of birth was sent and stored. It strips or stops, as at HEAD."""
    try:
        first = identifiers.strip(text, CFG.courses)
        out = first if mode == "read" else identifiers.strip(first, CFG.courses, marker_fills_slot=True)
    except identifiers.UnresolvedIdentifier:
        return
    assert not any(d in out for d in R9_DATES), out
    assert "FEB11010" in out


@pytest.mark.parametrize("text", R9_BORN)
def test_a_born_label_in_returned_fields_loses_the_date(text: str) -> None:
    out = identifiers.strip(text, CFG.courses, strict=False)
    assert not any(d in out for d in R9_DATES), out


@pytest.mark.parametrize(
    "text",
    [
        "Programme code:\t\t50952\tStudent number:\t\t598431\n",
        "Student number:\t\t598431\tProgramme code:\t\t50952\n",
        "Ref:\t\t598431\tStudent number:\t\t512345\n",
        "Student number:\t\t598431\tIssued:\t\t30-06-2024\n",
    ],
)
def test_a_colon_key_across_empty_cells_is_a_key(text: str) -> None:
    """r9 F4 LOW: with tab alignment the key of a non-label pair sat across empty cells, so the pair was not
    recognised and the import stopped. The label's value strips; the other pair's value is kept."""
    out = identifiers.strip(text, CFG.courses)
    assert CFG.courses.removed_marker in out, out
    kept = {"50952", "30-06-2024"} | ({"598431"} if text.startswith("Ref") else set())
    assert all(v in out for v in kept if v in text), out


TABLE = "FEB11001\tMathematics I\t8,0\t4\nFEB11002\tStatistics\t7,0\t4\nFEB11010\tEconometrics I\t7,5\t6\n"
R9_ROWS = [
    {"name": "Mathematics I", "code": "FEB11001", "grade": "8,0", "credits": 4.0},
    {"name": "Statistics", "code": "FEB11002", "grade": "7,0", "credits": 4.0},
    {"name": "Econometrics I", "code": "FEB11010", "grade": "7,5", "credits": 6.0},
]


def _r9_rows(**econometrics: object) -> list[dict]:
    base = {"term": None, "institution": None, "lecturers": [], "description": ""}
    rows = [{**base, **r} for r in R9_ROWS]
    rows[2].update(econometrics)
    return rows


@pytest.mark.parametrize(
    ("appendix", "description"),
    [
        (
            "FEB11010 Econometrics I\n"
            "This course builds on Mathematics I and Statistics: regression and inference.\n",
            "This course builds on Mathematics I and Statistics: regression and inference.",
        ),
        (
            "FEB11010 Econometrics I\nRegression and inference, with the\n"
            "prerequisite FEB11001 Mathematics I.\n",
            "Regression and inference, with the prerequisite FEB11001 Mathematics I.",
        ),
        ("FEB11010 Econometrics I\n\nLinear regression and inference.\n", "Linear regression and inference."),
        (
            "FEB11010 Econometrics I\nLinear regression.\n\nPanel data and inference.\n",
            "Linear regression.\n\nPanel data and inference.",
        ),
        ("Econometrics I\nLinear regression and inference.\n", "Linear regression and inference."),
        (
            "FEB11010 Econometrics I\nRegression after the\nGFC 2008 and ISO 9001 audits.\n",
            "Regression after the GFC 2008 and ISO 9001 audits.",
        ),
    ],
)
def test_a_described_course_keeps_its_own_description(appendix: str, description: str) -> None:
    """r9 F2 MEDIUM (my regression in the r3 N2 fix): a correct description was flagged when it named another
    course, held a code-shaped token, had a blank line after its heading or two paragraphs, or stood under a
    heading without the code."""
    text = f"{TABLE}\nCourse descriptions\n{appendix}"
    rows = _r9_rows(description=description)
    assert importer.DESCRIPTION_NOT_FOUND not in importer.row_problems(rows[2], text, [], CFG.courses, rows)


def test_a_description_appendix_still_refuses_another_courses_description() -> None:
    text = (
        f"{TABLE}\nCourse descriptions\nFEB11010 Econometrics I\n\nLinear regression and inference.\n\n"
        "FEB11002 Statistics\n\nProbability and estimation.\n"
    )
    rows = _r9_rows(description="Probability and estimation.")
    assert importer.DESCRIPTION_NOT_FOUND in importer.row_problems(rows[2], text, [], CFG.courses, rows)


@pytest.mark.parametrize(
    ("text", "lecturers"),
    [
        (
            "FEB11001\tMathematics I\t8,0\t4\nLecturer: Prof. dr. A. van den Berg (room HB 1012)\n",
            ["Prof. dr. A. van den Berg"],
        ),
        (
            "FEB11001\tMathematics I\t8,0\t4\nLecturer: Dr. J. Smit (Statistics group)\n"
            "FEB11002\tStatistics\t7,0\t4\n",
            ["Dr. J. Smit"],
        ),
    ],
)
def test_a_lecturer_line_mentioning_a_code_or_course_is_still_the_courses(
    text: str, lecturers: list[str]
) -> None:
    """r9 F3 LOW (my regression in the r3 N1 fix): a mention of a code-shaped token or another course on the
    lecturer's line ended the course's block."""
    rows = _r9_rows()
    maths = dict(rows[0], lecturers=lecturers)
    rows[0] = maths
    assert importer.LECTURER_NOT_FOUND not in importer.kinds(
        importer.row_problems(maths, text, [], CFG.courses, rows)
    )


# ---- close-out (session 4): whole values (ai-output r3 N3) and the lecturer named (ui r6 W1) -------


def _row_by_code(code: str, **changes: object) -> dict:
    row = next(r for r in EXPECTED["courses"] if r["code"] == code)
    return {**row, "description": "", **changes}


def _stripped(fmt: str) -> str:
    return identifiers.strip(_text(fmt), CFG.courses)


@pytest.mark.parametrize("fmt", list(FORMATS))
@pytest.mark.parametrize(
    ("code", "short"),
    [("FEB11005", "Mathematics"), ("FIN 3300", "Financial Modeling"), ("FEB13003", "Bachelor Thesis")],
)
def test_a_course_name_cut_short_is_flagged(fmt: str, code: str, short: str) -> None:
    """N3: a returned name that is a proper prefix of the longer name on its row ("Mathematics" where the row
    says "Mathematics II") passed as written; it is flagged on the name field."""
    problems = importer.row_problems(_row_by_code(code, name=short), _stripped(fmt), [], CFG.courses)
    assert importer.NAME_CUT_SHORT in problems, problems
    assert importer.problem_field(importer.NAME_CUT_SHORT) == "name"


@pytest.mark.parametrize(
    "line",
    [
        "Mathematics I FEB11001 8,0 4\n",  # name first, the code after it
        "Mathematics I 2021-2022 block 1 8,0 4\n",  # the term between the name and the grade
        "FEB11001\tMathematics I\t8,0\t4\n",
        "FEB11001 Mathematics I 8,0 4 Prof. dr. A. van den Berg\n",
    ],
)
def test_a_whole_course_name_is_not_cut_short(line: str) -> None:
    row = {
        "name": "Mathematics I",
        "code": "FEB11001",
        "grade": "8,0",
        "credits": 4.0,
        "term": None,
        "institution": None,
        "lecturers": [],
        "description": "",
    }
    assert importer.NAME_CUT_SHORT not in importer.row_problems(row, line, [], CFG.courses)


@pytest.mark.parametrize("fmt", list(FORMATS))
@pytest.mark.parametrize(
    "lecturer",
    [
        "Prof. dr. A. van den Berg; Dr. J. Smit",  # two names merged into one
        "Prof. dr. A. van den",  # ends in a particle
        "Prof. dr. A.",  # ends in an initial
    ],
)
def test_a_lecturer_that_is_not_one_whole_name_is_flagged(fmt: str, lecturer: str) -> None:
    """N3: a merged ("A; B") or truncated lecturer stood in the document as words and passed."""
    problems = importer.row_problems(
        _row_by_code("FEB11001", lecturers=[lecturer]), _stripped(fmt), [], CFG.courses
    )
    assert (
        f'Lecturer "{lecturer}" is not one whole name as written (cut off, or two names joined)' in problems
    )
    assert importer.LECTURER_NOT_FOUND in importer.kinds(problems)


@pytest.mark.parametrize("fmt", list(FORMATS))
def test_the_lecturer_problem_names_the_lecturer(fmt: str) -> None:
    """ui r6 W1: "A lecturer not found …" didn't say which; each lecturer not found gets its own message."""
    row = _row_by_code("FEB11001", lecturers=["Prof. dr. A. van den Berg", "Dr. Q. Invented"])
    problems = importer.row_problems(row, _stripped(fmt), [], CFG.courses)
    assert problems == ['Lecturer "Dr. Q. Invented" not found in the document as written'], problems
    assert importer.problem_field(problems[0]) == "lecturers"


def test_the_preview_shows_the_lecturer_by_name(client: FormClient, fake_rows) -> None:
    rows = [dict(r, description="") for r in EXPECTED["courses"]]
    rows[0]["lecturers"] = [*rows[0]["lecturers"], "Dr. Q. Invented"]
    fake_rows({"courses": rows, "warnings": []})
    html = _upload(client, "txt").text
    assert (
        "Lecturer &#34;Dr. Q. Invented&#34; not found" in html
        or 'Lecturer "Dr. Q. Invented" not found' in html
    )
    area = re.search(r'<textarea id="row-0-lecturers"[^>]*>', html).group(0)
    assert 'aria-invalid="true"' in area and "aria-describedby=" in area


# ---- data-integrity review round 10 (close-out) ------------------------------------------------------------

R10_BORN = [
    f"     Born\t12-03-2001\n{ROW}\n",
    f"      Born\n12-03-2001\n{ROW}\n",
    f"Name\t     Born\nNoor Example\t12-03-2001\n{ROW}\n",
    f"Born -- 12-03-2001\n{ROW}\n",
    f"Born - (12-03-2001)\n{ROW}\n",
    f"Born, (12-03-2001)\n{ROW}\n",
    f"Born .. 12-03-2001\n{ROW}\n",
    f"Born -, 12-03-2001\n{ROW}\n",
    f"Born.: 12-03-2001\n{ROW}\n",
    f"Born #: 12-03-2001\n{ROW}\n",
    f"Born (dd-mm-yyyy): 12-03-2001\n{ROW}\n",
    f"Born (date): 12-03-2001\n{ROW}\n",
    f"Geboren (datum): 12-03-2001\n{ROW}\n",
    f"Born (dd/mm/yyyy)\t12-03-2001\n{ROW}\n",
    f"Geboren (dd-mm-jjjj)\t12-03-2001\n{ROW}\n",
    f"Name\tBorn (dd-mm-yyyy)\nNoor Example\t12-03-2001\n{ROW}\n",
    # not blocking in r10, closed by the same rule
    f"Born 12 Maerz 2001\n{ROW}\n",
    f"Born \u2014 12-03-2001\n{ROW}\n",
    f"Born / 12-03-2001\n{ROW}\n",
    f"Born/Geboren\t12-03-2001\n{ROW}\n",
    f"- Born\t12-03-2001\n{ROW}\n",
    f"1. Born\t12-03-2001\n{ROW}\n",
    f"Born.\t12-03-2001\n{ROW}\n",
    f"Noor Example (born 12-03-2001)\n{ROW}\n",
    f"Born at Rotterdam, 12-03-2001\n{ROW}\n",
]
R10_DATES = ("12-03-2001", "12 Maerz 2001")


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize("text", R10_BORN)
def test_born_is_a_label_unless_a_name_or_a_word_says_otherwise(text: str, mode: str) -> None:
    """r10 F1 HIGH: "born"/"geboren" is a label by default; it is no label only after a title, initial or
    particle ("Prof. M. Born") or before a word that is not a month ("Born rule"). The date goes or stops."""
    try:
        first = identifiers.strip(text, CFG.courses)
        out = first if mode == "read" else identifiers.strip(first, CFG.courses, marker_fills_slot=True)
    except identifiers.UnresolvedIdentifier:
        return
    assert not any(d in out for d in R10_DATES), out
    assert "FEB11010" in out


@pytest.mark.parametrize("text", R10_BORN)
def test_born_in_returned_fields_loses_the_date(text: str) -> None:
    out = identifiers.strip(text, CFG.courses, strict=False)
    assert not any(d in out for d in R10_DATES), out


@pytest.mark.parametrize(
    "text",
    [
        "PHY 2010 Quantum Mechanics 8 2 Prof. M. Born 8,0 4\n",  # a lecturer Born before a grade, untabbed
        "PHY 2010\tQuantum Mechanics\tProf. dr. ir. M. Born\n",
        "Lecturer: Dr. M. van Born\n",
    ],
)
def test_a_lecturer_born_after_an_initial_title_or_particle_is_no_label(text: str) -> None:
    assert identifiers.strip(text, CFG.courses) == text


R10_HEADINGS = [
    "Mathematics I (4 EC)\n",
    "1. Mathematics I\n",
    "Mathematics I:\n",
    "Course: Mathematics I\n",
    "FEB11001\n",
]


@pytest.mark.parametrize("heading", R10_HEADINGS)
def test_a_description_under_any_heading_of_its_course_is_kept(heading: str) -> None:
    """r10 F2 MEDIUM: a correct description under a heading naming the course in another form was flagged
    (the course not last in its table). A description is flagged only when it is not in the document, or
    stands in another returned course's block."""
    text = f"{TABLE}\nCourse descriptions\n{heading}Calculus and linear algebra.\n"
    rows = _r9_rows()
    maths = dict(rows[0], description="Calculus and linear algebra.")
    rows[0] = maths
    assert importer.DESCRIPTION_NOT_FOUND not in importer.row_problems(maths, text, [], CFG.courses, rows)


def test_a_description_on_its_heading_line_is_kept() -> None:
    text = f"{TABLE}\nMathematics I: Calculus and linear algebra.\n"
    rows = _r9_rows()
    maths = dict(rows[0], description="Calculus and linear algebra.")
    rows[0] = maths
    assert importer.DESCRIPTION_NOT_FOUND not in importer.row_problems(maths, text, [], CFG.courses, rows)


# ---- real-transcript finding (2026-11-08): unlabelled footer, brackets, wrapped names, own institution

OWNER = "Noor Example"  # the fixture's invented student


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize(
    ("line", "gone", "kept"),
    [
        ("J.A. Example (512345)   1/9\n", ["512345"], ["1/9"]),
        ("N. Example 512345   2/9\n", ["512345"], ["2/9"]),
        ("N. Example 512345ab\n", ["512345ab"], []),
        ("NOOR EXAMPLE, 12-03-2001\n", ["12-03-2001"], []),
        ("Example, N. - S2034567\n", ["S2034567"], []),
        ("Noor Example 598 431 12\n", ["598 431"], []),
        (
            "Courses taken by N. Example: FEB11001 2021-2022 ( 20.0 )\n",
            [],
            ["FEB11001", "2021-2022", "( 20.0 )"],
        ),
    ],
)
def test_on_a_line_with_the_owners_name_every_id_shaped_token_goes(
    line: str, gone: list[str], kept: list[str], mode: str
) -> None:
    """Rule 1 (real transcript: a PDF footer "<initials> <surname> (<student number>)   n/9" with no label was
    sent): on a line holding the owner's full name, surname or initials and surname, every ID-shaped token
    (5+ digits, digits with a short suffix, a date) goes; course codes, year ranges and credits stay."""
    first = identifiers.strip(line, CFG.courses, owner=OWNER)
    out = (
        first
        if mode == "read"
        else identifiers.strip(first, CFG.courses, marker_fills_slot=True, owner=OWNER)
    )
    assert out == first
    assert not any(v in out for v in gone), out
    assert all(v in out for v in kept), out


def test_without_the_owners_name_an_unlabelled_number_stays() -> None:
    """Control: rule 1 is anchored on the owner's name (rules 2 and 3 cover other shapes)."""
    assert identifiers.strip("Archive reference 987654321\n", CFG.courses, owner=OWNER) == (
        "Archive reference 987654321\n"
    )


@pytest.mark.parametrize(
    ("line", "gone"),
    [
        ("Some Person (512345)\n", "512345"),
        ("Ref. (S2034567) on file\n", "S2034567"),
        ("Issued (12-03-2001)\n", "12-03-2001"),
        ("Footer ( 598431 )\n", "598431"),
        ("Number (512345ab)\n", "512345ab"),
    ],
)
def test_a_bracket_holding_only_an_id_shaped_token_goes(line: str, gone: str) -> None:
    """Rule 2: wherever it stands, a bracket holding only an ID-shaped token loses it."""
    out = identifiers.strip(line, CFG.courses)
    assert gone not in out and f"({CFG.courses.removed_marker})" in out.replace(" ", ""), out


@pytest.mark.parametrize(
    "line",
    [
        "FEB11001\tMathematics I\t8,0\t( 20.0 )\n",
        "FEB11001 Mathematics I 8,0 ( 5.0 )\n",
        "Thesis (FEB13003) 8,0 15\n",
        "Exchange (FIN 3010)\n",
        "Academic year (2021-2022)\n",
        "Credits (30)\n",
    ],
)
def test_bracketed_credits_codes_and_years_are_kept(line: str) -> None:
    assert identifiers.strip(line, CFG.courses) == line


def test_a_repeated_page_footer_loses_its_id_shaped_tokens() -> None:
    """Rule 3: a line identical apart from its page number on 2+ pages is a header/footer; its ID-shaped
    tokens go even without a label or the owner's name."""
    text = "Transcript 598431 - page 1 of 2\nFEB11001 Mathematics I 8,0 4\nTranscript 598431 - page 2 of 2\n"
    out = identifiers.strip(text, CFG.courses)
    assert "598431" not in out and "FEB11001 Mathematics I 8,0 4" in out, out


@pytest.mark.parametrize(
    "text",
    [
        "Transcript 598431 - page 1 of 2\nFEB11001 Mathematics I 8,0 4\n",  # once: not a repeated footer
        "FEB11001 Mathematics I 8,0 4\nFEB11001 Mathematics I 8,0 4\n",  # repeated, no page number
    ],
)
def test_a_line_without_a_repeated_page_number_is_no_footer(text: str) -> None:
    assert identifiers.strip(text, CFG.courses) == text


def _course_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if re.match(r"(FEB|FIN|ECON|STAT|ACCT|COMM)\s?\d", line)]


@pytest.mark.parametrize("fmt", list(FORMATS))
def test_the_fixture_footer_never_reaches_claude_and_course_rows_are_unchanged(fmt: str) -> None:
    """The eval fixture prints "N. Example (512345)   n/N" on every page (all three formats); after stripping
    no 512345 is left anywhere, and every course row stands exactly as printed."""
    raw = _text(fmt)
    assert raw.count("N. Example (512345)") >= 2, "the footer is on every page"
    out = identifiers.strip(raw, CFG.courses, owner=OWNER)
    assert "512345" not in out
    assert _course_lines(out) == _course_lines(raw) and len(_course_lines(raw)) >= 28


WRAPPED = (
    "WRP1001 Professional Development & 7,5 5 Dr. J. Smit\nMentoring I\n"
    "WRP1002 Statistics II 8,0 5 Dr. J. Smit\n"
)
WRAPPED_TABBED = (
    "WRP1001\tProfessional Development &\t7,5\t5\tDr. J. Smit\n\tMentoring I\t\t\t\n"
    "WRP1002\tStatistics II\t8,0\t5\tDr. J. Smit\n"
)


def _wrapped_row(name: str = "Professional Development & Mentoring I") -> dict:
    return {
        "name": name,
        "code": "WRP1001",
        "grade": "7,5",
        "credits": 5.0,
        "term": None,
        "institution": None,
        "lecturers": ["Dr. J. Smit"],
        "description": "",
    }


@pytest.mark.parametrize("text", [WRAPPED, WRAPPED_TABBED], ids=["untabbed", "tabbed"])
def test_a_course_name_wrapped_onto_the_next_line_counts_as_written(text: str) -> None:
    """Rule 4: "Professional Development &" / "Mentoring I" in the same column is the name as written when
    the joined text matches it exactly."""
    assert importer.row_problems(_wrapped_row(), text, [], CFG.courses) == []


@pytest.mark.parametrize("text", [WRAPPED, WRAPPED_TABBED], ids=["untabbed", "tabbed"])
@pytest.mark.parametrize(
    "name",
    [
        "Professional Development & Mentoring",  # the tail cut short
        "Professional Development & Mentoring II",  # not as written
        "Professional Development and Mentoring I",  # reworded
        "Professional Development & Statistics II",  # the next course's name
    ],
)
def test_a_wrapped_name_must_still_match_verbatim(text: str, name: str) -> None:
    problems = importer.row_problems(_wrapped_row(name), text, [], CFG.courses)
    assert importer.COURSE_NOT_FOUND in problems or importer.NAME_CUT_SHORT in problems, problems


@pytest.mark.parametrize("name", ["wrapped.txt", "wrapped.pdf"])
def test_the_wrapped_fixture_imports_its_wrapped_course(name: str) -> None:
    """A separate invented fixture (tests/evals/courses/wrapped.*), so the 28-course eval set is unchanged;
    it also prints the owner footer on each of its pages."""
    raw = (EVAL / name).read_bytes()
    if name.endswith(".txt"):
        text = extract.text_from_paste(raw.decode("utf-8"))
    else:
        text = extract.extract_text(name, raw, CFG.courses)
    assert text.count("N. Example (512345)") >= 2
    text = identifiers.strip(text, CFG.courses, owner=OWNER)
    assert "512345" not in text
    assert importer.row_problems(_wrapped_row(), text, [], CFG.courses) == [], text


@pytest.mark.parametrize("step", ["review", "preview"])
def test_cancel_writes_nothing(client: FormClient, data_dir: Path, fake_rows, step: str) -> None:
    """Cancel (a link back to /courses/import) after the review or after the preview writes nothing to
    sources/ or courses.yaml."""
    page = _read(client, "pdf")
    assert page.status_code == 200
    if step == "preview":
        page = _send(client, page.text)
        assert page.status_code == 200
    assert 'href="/courses/import"' in page.text
    assert client.get("/courses/import").status_code == 200
    assert not (data_dir / "courses.yaml").exists()
    assert not (data_dir / "sources").exists() or not list((data_dir / "sources").iterdir())


def _nfu_without_institution() -> list[dict]:
    rows = [dict(r, description="") for r in EXPECTED["courses"]]
    for r in rows:
        if r["institution"] == "Northfield University":
            r["institution"] = None
    return rows


def test_an_owner_entered_institution_fills_empty_rows_only(
    client: FormClient, data_dir: Path, fake_rows
) -> None:
    """Item 5: "Institution for these courses (your own entry)" fills every row whose institution is empty;
    stored as owner-entered, so the as-written check doesn't apply; rows with a document institution keep
    theirs."""
    fake_rows({"courses": _nfu_without_institution(), "warnings": []})
    html = _upload(client, "txt").text
    assert "Institution for these courses (your own entry)" in html
    fields = [(k, v) for k, v in form_fields(html, "courses-preview") if k != "owner_institution"]
    fields.append(("owner_institution", "Northfield Univ. (my entry)"))
    res = client.post("/courses/import/save", data=[*fields, ("accept", "all")])
    assert res.status_code == 200, res.text[-600:]
    stored = YAML(typ="safe").load((data_dir / "courses.yaml").read_text(encoding="utf-8"))["courses"]
    assert len(stored) == 28
    by_code = {c["code"]: c for c in stored}
    assert by_code["FIN 3010"]["institution"] == "Northfield Univ. (my entry)"
    assert by_code["FIN 3010"]["institution_entered"] is True
    assert by_code["FEB11001"]["institution"] == "Example School of Economics"
    assert by_code["FEB11001"].get("institution_entered", False) is False


def test_an_owner_entered_institution_survives_check_again_and_is_never_flagged(
    client: FormClient, fake_rows, calls: Path
) -> None:
    fake_rows({"courses": _nfu_without_institution(), "warnings": []})
    html = _upload(client, "txt").text
    sent = calls.read_text(encoding="utf-8")
    fields = [(k, v) for k, v in form_fields(html, "courses-preview") if k != "owner_institution"]
    fields.append(("owner_institution", "Invented Institute"))
    again = client.post("/courses/import/save", data=[*fields, ("accept", "check")]).text
    assert importer.INSTITUTION_NOT_FOUND not in again
    assert 'value="Invented Institute"' in again
    assert calls.read_text(encoding="utf-8") == sent  # nothing more sent to Claude
    assert "Invented Institute" not in sent


# ---- user decision on data-integrity r11 F3 and F6 (session 5, DECISIONS #100) -------------------------


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize(
    ("line", "gone", "kept"),
    [
        ("Born Rotterdam, 12-03-2001\n", "12-03-2001", "Born Rotterdam,"),
        ("Born Rotterdam 12 March 2001\n", "12 March 2001", "Born Rotterdam"),
        ("Born - Rotterdam - 12-03-2001\n", "12-03-2001", "Rotterdam"),
        ("Born, Rotterdam, 12-03-2001\n", "12-03-2001", "Rotterdam"),
        ("geboren te Sofia op 12-03-2001\n", "12-03-2001", "Sofia"),
        ("Geboren in Den Haag op 12 maart 2001\n", "12 maart 2001", "Den Haag"),
        ("born in Utrecht city centre 2001-03-12\n", "2001-03-12", "Utrecht"),  # 4 words between
        # a lecturer named Born, then a completion date: the date goes (user: accepted, the row is flagged)
        ("PHY 2010\tQuantum Mechanics\t8\t2\tProf. M. Born\t12-01-2022\n", "12-01-2022", "Prof. M. Born"),
    ],
)
def test_a_date_within_four_words_after_born_goes(line: str, gone: str, kept: str, mode: str) -> None:
    """r11 F3 (user decision): a date that follows "born"/"geboren" on the same line within 4 words is
    removed, whatever stands before the word (a title, an initial) or between (a place)."""
    first = identifiers.strip(line, CFG.courses, owner=OWNER)
    out = (
        first
        if mode == "read"
        else identifiers.strip(first, CFG.courses, marker_fills_slot=True, owner=OWNER)
    )
    assert out == first
    assert gone not in out and CFG.courses.removed_marker in out and kept in out, out


@pytest.mark.parametrize(
    "line",
    [
        "Born to a family of farmers near the sea, 12-03-2001\n",  # 8 words between: not this rule
        "Prof. M. Born\tQuantum Mechanics\t8\t2\n",  # no date
        "The Born rule (1926) was covered.\n",  # a year is no date shape
        "Lecturer: Dr. K. Geboren, office 12\n",
    ],
)
def test_born_without_a_near_date_is_left_alone(line: str) -> None:
    assert identifiers.strip(line, CFG.courses, owner=OWNER) == line


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize(
    "line",
    [
        "Dr. Born: 12-03-2001\n",
        "Prof. M. Born: 12-03-2001\n",
        "M. van Born : 12-03-2001\n",
        "Dr. K. Geboren: 12 maart 2001\n",
    ],
)
def test_a_colon_after_born_always_makes_it_a_label(line: str, mode: str) -> None:
    """r11 F3 (user decision): as the config comment says, a colon directly after "born"/"geboren" makes it a
    label even after a title, an initial or a particle; its value goes."""
    first = identifiers.strip(line, CFG.courses, owner=OWNER)
    out = (
        first
        if mode == "read"
        else identifiers.strip(first, CFG.courses, marker_fills_slot=True, owner=OWNER)
    )
    assert "2001" not in out and CFG.courses.removed_marker in out, out


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize(
    ("line", "gone", "kept"),
    [
        ("N. Example 512345-01\n", "512345-01", "N. Example"),  # rule 1, a line naming the owner
        ("Ref. (512345-01)\n", "512345-01", "Ref. ("),  # rule 2, a bracket holding only it
        ("Ref. (5123456-123) on file\n", "5123456-123", "on file"),
        (
            "Transcript for 512345-01   page 1\nFEB11001\tMathematics I\t8,0\t4\n"
            "Transcript for 512345-01   page 2\n",
            "512345-01",
            "page 2",
        ),  # rule 3, a repeated footer
    ],
)
def test_an_id_with_a_short_hyphen_suffix_goes(line: str, gone: str, kept: str, mode: str) -> None:
    """r11 F6 (user decision): the #93 ID shape takes an optional hyphen and 1–3 digits ("512345-01")."""
    first = identifiers.strip(line, CFG.courses, owner=OWNER)
    out = (
        first
        if mode == "read"
        else identifiers.strip(first, CFG.courses, marker_fills_slot=True, owner=OWNER)
    )
    assert out == first
    assert gone not in out and kept in out, out


@pytest.mark.parametrize(
    "line",
    [
        "N. Example: FEB11001 2021-2022 ( 20.0 )\n",  # a year range, a course code, credits
        "Ref. (2021-2022)\n",
        "Ref. (FEB-11001)\n",
        "Ref. (512345-2022)\n",  # a 4-digit suffix is not this shape
        "Ref. (3010-01)\n",  # a course code with a section: 4 digits before the suffix
    ],
)
def test_year_ranges_and_codes_stay_with_the_suffix_shape(line: str) -> None:
    assert identifiers.strip(line, CFG.courses, owner=OWNER) == line


def test_a_born_label_with_its_date_beyond_four_words_still_stops() -> None:
    """Control for #100: "born in" is a label (#89); with the date 5 words on, rule 4 doesn't settle it and
    the import stops as before (nothing is sent)."""
    with pytest.raises(identifiers.UnresolvedIdentifier):
        identifiers.strip("born in the city of Utrecht 2001-03-12\n", CFG.courses, owner=OWNER)


def test_born_settles_only_when_nothing_else_id_shaped_is_on_the_line() -> None:
    """Control for #100: another 5+ digit number on the line keeps the stop (fail closed)."""
    with pytest.raises(identifiers.UnresolvedIdentifier):
        identifiers.strip("geboren te Sofia op 12-03-2001 598431\n", CFG.courses, owner=OWNER)
