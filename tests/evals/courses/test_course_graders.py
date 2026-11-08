"""The course-import graders, offline (C-2c-03 support): a perfect extraction passes; each kind of error,
the planted swap and an identifier in the output are caught."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import course_graders
import pytest

from hub.config import load_config
from hub.courses import extract, identifiers, importer
from hub.models.course import TranscriptCourse

HERE = Path(__file__).resolve().parent
EXPECTED = json.loads((HERE / "expected.json").read_text(encoding="utf-8"))
CFG = load_config()
TEXT = identifiers.strip(
    extract.text_from_paste((HERE / "transcript.txt").read_text(encoding="utf-8")), CFG.courses
)


def _rows() -> list[dict]:
    return [{**copy.deepcopy(r), "description": ""} for r in EXPECTED["courses"]]


def test_a_perfect_extraction_passes() -> None:
    report = course_graders.grade(_rows(), TEXT, EXPECTED)
    keys = ("name", "code", "institution", "grade", "credits", "term", "lecturers")
    courses = [
        TranscriptCourse(id=f"c{i:02d}", **{k: r[k] for k in keys}) for i, r in enumerate(EXPECTED["courses"])
    ]
    report |= course_graders.lecturers_graded(courses, EXPECTED, CFG.names, CFG.courses)
    report["identifiers_in_data_dir"] = []
    assert course_graders.passed(report), report
    assert report["swap_caught"] is True and report["lecturers_listed"] == len(EXPECTED["lecturers"])


@pytest.mark.parametrize(
    ("change", "metric"),
    [
        (lambda rows: rows.pop(3), "missing_courses"),
        (lambda rows: rows[0].update(name="Mathematics 1"), "name_mismatches"),
        (lambda rows: rows[1].update(grade="7.5"), "grade_mismatches"),
        (lambda rows: rows[2].update(credits=5.0), "credit_mismatches"),
        (lambda rows: rows[4].update(description="Student number 512345"), "identifiers_in_output"),
        (lambda rows: rows.append({**rows[0], "code": "XX1"}), "extra_rows"),
    ],
)
def test_each_error_is_caught(change, metric: str) -> None:  # noqa: ANN001
    rows = _rows()
    change(rows)
    report = course_graders.grade(rows, TEXT, EXPECTED)
    assert report[metric], report
    assert not course_graders.passed({**report, "lecturer_mismatches": [], "identifiers_in_data_dir": []})


def test_a_swapped_grade_counts_as_wrong_and_flagged() -> None:
    rows = _rows()
    swapped = next(r for r in rows if r["code"] == EXPECTED["swap"]["code"])
    swapped["grade"] = EXPECTED["swap"]["wrong_grade"]
    report = course_graders.grade(rows, TEXT, EXPECTED)
    assert report["grade_mismatches"] and not report["unflagged_wrong_grades"]


def test_identifiers_are_found_in_any_separator_form() -> None:
    assert course_graders.identifiers_in("bsn 123-456-782", ["123456782"]) == ["123456782"]
    assert course_graders.identifiers_in("nothing here", EXPECTED["identifiers"]) == []


def test_a_wrong_grade_printed_elsewhere_on_its_own_row_is_counted_unflagged() -> None:
    """The row check can't catch a wrong grade that really stands on the course's row (here an extra "6,5"
    column); the grader still counts it, so the live gate never relies on the row check alone."""
    text = TEXT.replace("FEB11002	Microeconomics	7,5", "FEB11002	Microeconomics	7,5	6,5")
    rows = _rows()
    rows[1]["grade"] = "6,5"
    report = course_graders.grade(rows, text, EXPECTED)
    assert report["unflagged_wrong_grades"] == ["FEB11002"]


def test_a_grade_equal_to_the_credits_is_flagged_now() -> None:
    rows = _rows()
    rows[1]["grade"] = "6"  # Microeconomics' credits: the row check takes them out first (review fix)
    report = course_graders.grade(rows, TEXT, EXPECTED)
    assert report["grade_mismatches"] and not report["unflagged_wrong_grades"]


# ---- ai-output review round 2, F2 (session 3, DECISIONS #86) ---


def _rows_with(n: int, **changes: object) -> list[dict]:
    rows = [{**r, "description": ""} for r in EXPECTED["courses"]]
    rows[n] = {**rows[n], **changes}
    return rows


@pytest.mark.parametrize(
    ("changes", "key"),
    [
        ({"description": "An invented description."}, "description_mismatches"),
        ({"term": "2021-2022"}, "term_mismatches"),
        ({"institution": "Example School"}, "institution_mismatches"),
        ({"lecturers": ["A. van den Berg", "Dr. J. Smit"]}, "lecturer_text_mismatches"),
        ({"lecturers": ["Dr. J. Smit", "Prof. dr. A. van den Berg"]}, "lecturer_text_mismatches"),
    ],
)
def test_the_grader_catches_text_not_as_written(changes: dict, key: str) -> None:
    report = course_graders.grade(_rows_with(0, **changes), TEXT, EXPECTED)
    assert report[key], report
    assert not course_graders.passed({**report, "lecturer_mismatches": [], "identifiers_in_data_dir": []})


def test_the_grader_passes_the_rows_as_written() -> None:
    report = course_graders.grade(_rows_with(0), TEXT, EXPECTED)
    for key in (
        "description_mismatches",
        "term_mismatches",
        "institution_mismatches",
        "lecturer_text_mismatches",
    ):
        assert report[key] == [], (key, report[key])


def test_a_correct_row_flagged_for_any_problem_is_a_false_problem() -> None:
    """ai-output r3 N5: `false_flags` counted grade flags only, so a correct row blocked for another reason
    (here its lecturer, missing from the text) left the report "passed". Every problem on a correct row is
    now a false problem, and it fails the grade."""
    text = TEXT.replace("Prof. dr. K. Müller", "Prof. dr. K. Example")
    report = course_graders.grade(_rows(), text, EXPECTED)
    taught = [r["code"] for r in EXPECTED["courses"] if "Prof. dr. K. Müller" in r["lecturers"]]
    assert len(taught) == 2
    want = [
        {"code": code, "problems": [importer.lecturer_not_found("Prof. dr. K. Müller")]} for code in taught
    ]
    assert sorted(report["false_problems"], key=lambda f: f["code"]) == sorted(want, key=lambda f: f["code"])
    assert not course_graders.passed({**report, "lecturer_mismatches": [], "identifiers_in_data_dir": []})
