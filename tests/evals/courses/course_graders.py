"""Graders for the course-import eval (C-2c-03; docs/04 §9). Pure Python, no model.

``grade(rows, stripped_text, expected)`` compares the extracted rows (already stripped again) with
``expected.json``:
- ``missing_courses`` / ``extra_rows``: expected codes not found / rows with no expected course;
- ``name_mismatches``, ``grade_mismatches``, ``credit_mismatches``: exact name, exact grade string, credits;
- ``false_flags``: correct grades that the app's row-anchored check would flag (must be 0);
- ``unflagged_wrong_grades``: wrong grades the check would let through (must be 0);
- ``swap_caught``: the planted swap (a course given another course's grade, printed elsewhere) is flagged;
- ``identifiers_in_output``: identifiers from ``expected.json`` found in any returned field;
- ``description_mismatches``, ``term_mismatches``, ``institution_mismatches``, ``lecturer_text_mismatches``:
  each copied exactly as written (the fixture has no descriptions, so "" is the only right one; lecturers
  as written, titles and order kept; rule 15, DECISIONS #86).
Lecturer grouping is graded on the stored courses with ``lecturers_graded``.
"""

from __future__ import annotations

import re
from typing import Any

from hub.config import CoursesConfig, NamesConfig
from hub.courses import importer, lecturers
from hub.models.course import TranscriptCourse


def alnum(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", text.casefold())


def identifiers_in(text: str, identifiers: list[str]) -> list[str]:
    """Identifiers present as written, or (for numbers of 6+ characters) in any separator form."""
    found = []
    folded = alnum(text)
    for value in identifiers:
        if value in text or (
            any(c.isdigit() for c in value) and len(alnum(value)) >= 6 and alnum(value) in folded
        ):
            found.append(value)
    return found


def grade(rows: list[dict[str, Any]], text: str, expected: dict[str, Any]) -> dict[str, Any]:
    by_code = {r["code"]: r for r in expected["courses"]}
    got = {}
    extra = []
    for row in rows:
        code = (row.get("code") or "").strip()
        if code in by_code and code not in got:
            got[code] = row
        else:
            extra.append(code or row.get("name"))
    report: dict[str, Any] = {
        "rows": len(rows),
        "missing_courses": sorted(set(by_code) - set(got)),
        "extra_rows": extra,
        "name_mismatches": [],
        "grade_mismatches": [],
        "credit_mismatches": [],
        "false_flags": [],
        "false_problems": [],  # every problem the app raises on a row equal to the expected one (r3 N5)
        "unflagged_wrong_grades": [],
        "description_mismatches": [],
        "term_mismatches": [],
        "institution_mismatches": [],
        "lecturer_text_mismatches": [],
        "rows_returned": rows,  # the evidence, as returned (after the second strip)
    }
    for code, row in got.items():
        want = by_code[code]
        if row.get("name") != want["name"]:
            report["name_mismatches"].append({"code": code, "got": row.get("name"), "want": want["name"]})
        problems = importer.row_problems(row, text, [], None, rows)  # the app's own check, as the preview
        flagged = importer.GRADE_NOT_FOUND in problems
        if row.get("grade") != want["grade"]:
            report["grade_mismatches"].append({"code": code, "got": row.get("grade"), "want": want["grade"]})
            if not flagged:
                report["unflagged_wrong_grades"].append(code)
        elif flagged:
            report["false_flags"].append(code)
        # copied as written (rule 15; #86): the fixture gives no description, so "" is the only right one
        for key, got_value, want_value in (
            ("description_mismatches", row.get("description") or "", want.get("description", "")),
            ("term_mismatches", row.get("term"), want["term"]),
            ("institution_mismatches", row.get("institution"), want["institution"]),
            ("lecturer_text_mismatches", list(row.get("lecturers") or []), want["lecturers"]),
        ):
            if got_value != want_value:
                report[key].append({"code": code, "got": got_value, "want": want_value})
        correct = (
            all(row.get(k) == want[k] for k in ("name", "grade", "term", "institution"))
            and (row.get("description") or "") == want.get("description", "")
            and list(row.get("lecturers") or []) == want["lecturers"]
            and row.get("credits") is not None
            and abs(float(row["credits"]) - want["credits"]) <= 1e-9
        )
        if correct and problems:
            report["false_problems"].append({"code": code, "problems": problems})
        if row.get("credits") is None or abs(float(row["credits"]) - want["credits"]) > 1e-9:
            report["credit_mismatches"].append(
                {"code": code, "got": row.get("credits"), "want": want["credits"]}
            )
    swap = expected["swap"]
    target = by_code[swap["code"]]
    report["swap_caught"] = importer.GRADE_NOT_FOUND in importer.row_problems(
        dict(target, grade=swap["wrong_grade"]), text, [], None, rows
    )
    output_text = " ".join(
        str(v) for row in rows for v in [*row.values(), *(row.get("lecturers") or [])] if isinstance(v, str)
    )
    report["identifiers_in_output"] = identifiers_in(output_text, expected["identifiers"])
    return report


def lecturers_graded(
    courses: list[TranscriptCourse],
    expected: dict[str, Any],
    names_cfg: NamesConfig,
    courses_cfg: CoursesConfig,
) -> dict[str, Any]:
    """Every expected lecturer listed once with exactly their courses."""
    groups = lecturers.group(courses, names_cfg, courses_cfg)
    got = {
        f"{g.parsed.surname} {' '.join(g.parsed.initials)}": sorted(c.code or "" for c, _ in g.courses)
        for g in groups
    }
    want = {k: sorted(v) for k, v in expected["lecturers"].items()}
    return {
        "lecturers_listed": len(groups),
        "lecturer_mismatches": sorted(k for k in set(got) | set(want) if got.get(k) != want.get(k)),
    }


def passed(report: dict[str, Any]) -> bool:
    gated = ("missing_courses", "extra_rows", "name_mismatches", "grade_mismatches", "credit_mismatches",
             "false_flags", "false_problems", "unflagged_wrong_grades", "identifiers_in_output",
             "lecturer_mismatches", "identifiers_in_data_dir", "description_mismatches", "term_mismatches",
             "institution_mismatches", "lecturer_text_mismatches")  # fmt: skip
    return all(not report.get(k) for k in gated) and report.get("swap_caught") is True
