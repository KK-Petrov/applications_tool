"""From the model's rows to confirmed courses (docs/02 §10, docs/04 §9; rule 13; DECISIONS #44, #64).

- **Payload:** ``{"text": the identifier-stripped text, "institution_hint": null}``; nothing else.
- **Stripped again:** every field the model returns goes through the identifier stripping once more.
- **Grades are facts, anchored to their row:** a grade is accepted only if its exact string stands on the same
  line (a table row is one line) as the course's code **and** name (or, without the code on any line, its name
  on word boundaries), outside the row's own name, code, term and credits. The credits must stand on the row
  too. A grade printed elsewhere in the document (another course's) does not count; "8" doesn't
  match "8,5" and "A" doesn't match "A-". Otherwise the row is flagged "Grade not found in the document" and
  can't be accepted until the user corrects or clears the grade.
- **Duplicates:** a row with the same name, code, institution and term as a stored course is "Already
  imported" and can't be accepted.
"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, Any

from hub.courses import identifiers
from hub.models.course import TranscriptCourse
from hub.programme_form import slugify, unique_slug
from hub.rules.names import fold

if TYPE_CHECKING:
    from hub.config import CoursesConfig

GRADE_NOT_FOUND = "Grade not found in the document"
CREDITS_NOT_FOUND = "Credits not found on this course's row"
TERM_NOT_FOUND = "Term not found on this course's row"
COURSE_NOT_FOUND = "Course not found in the document"
ALREADY_IMPORTED = "Already imported"
ROW_FIELDS = ("name", "code", "institution", "grade", "credits", "term", "lecturers", "description")


def payload(text: str) -> dict[str, Any]:
    """The ``/extract-courses`` input: the stripped text and nothing else (docs/04 §9)."""
    return {"text": text, "institution_hint": None}


def strip_row(row: dict[str, Any], cfg: CoursesConfig) -> dict[str, Any]:
    """A returned row with every text field stripped of identifiers again (docs/02 §10)."""

    def clean(value: Any) -> Any:
        return identifiers.strip(value, cfg, strict=False) if isinstance(value, str) else value

    out = {k: clean(v) for k, v in row.items()}
    out["lecturers"] = [clean(v) for v in row.get("lecturers") or []]
    return out


def _word_pattern(text: str) -> re.Pattern[str]:
    words = text.split()
    return re.compile(r"(?<![\w])" + r"\s+".join(re.escape(w) for w in words) + r"(?![\w])", re.IGNORECASE)


def _lines_for(text: str, name: str, code: str | None, term: str | None = None) -> list[str]:
    """The course's rows; with a term, only rows that also hold it (a course taken twice has two rows)."""
    rows = _rows_for(text, name, code)
    if term and term.strip():
        with_term = [line for line in rows if term.strip().casefold() in " ".join(line.split()).casefold()]
        return with_term
    return rows


def _rows_for(text: str, name: str, code: str | None) -> list[str]:
    """The lines (table rows) that hold this course: its code **and** its name when both are given and the
    code is in the document (a row with another course's code is not this course); else its name."""
    lines = text.split("\n")
    has_name = bool(name.split())
    name_re = _word_pattern(name) if has_name else None
    if code and code.strip():
        code_re = re.compile(rf"(?<![\w]){re.escape(code.strip())}(?![\w])")
        with_code = [line for line in lines if code_re.search(line)]
        if with_code:
            return [line for line in with_code if name_re is None or name_re.search(line)]
    return [line for line in lines if name_re is not None and name_re.search(line)]


_NUMBER = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)?(?![\w])(?![.,]\d)")


def _number_spans_equal(line: str, value: float) -> list[tuple[int, int]]:
    """Where ``value`` stands on the line as a number, however it is printed ("3", "3.0", "3.00", "7,50")."""
    out = []
    for m in _NUMBER.finditer(line):
        try:
            if abs(float(m.group(0).replace(",", ".")) - value) < 1e-9:
                out.append(m.span())
        except ValueError:
            continue
    return out


def _credit_forms(credits: float) -> list[str]:
    """How a credit value may be printed: "6", "6.0", "6,0", "7.5", "7,5"."""
    forms = {f"{credits:g}", f"{credits:.1f}"}
    return sorted(forms | {f.replace(".", ",") for f in forms}, key=len, reverse=True)


def _number_pattern(value: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![0-9A-Za-z])(?<![0-9][.,]){re.escape(value)}(?![0-9A-Za-z])(?![.,][0-9])")


def _without_known_fields(
    line: str, name: str, code: str | None, term: str | None, credits: float | None
) -> str:
    """The row with its own name, code, term and one credits value taken out, so the grade can't be "found"
    in them (a grade equal to the credits, a year or a block number; ai-output review). A real grade equal to
    the credits is still found: the row then holds that number twice."""
    for part in (code, term):
        if part and part.strip():
            line = re.sub(re.escape(part.strip()), " ", line, count=1, flags=re.IGNORECASE)
    if name.split():
        line = _word_pattern(name).sub(" ", line, count=1)
    if credits is not None:
        spans = _number_spans_equal(line, float(credits))
        if spans:
            start, end = spans[0]
            line = line[:start] + " " + line[end:]
    return line


def _grade_pattern(grade: str) -> re.Pattern[str]:
    g = re.escape(grade)
    tail = r"(?![0-9A-Za-z])(?![.,][0-9])"
    if grade[-1:].isalpha():
        tail += r"(?![+\-](?![0-9A-Za-z]))(?!\.)"  # "A" is not "A-" / "A+" / the initial "A."
    return re.compile(rf"(?<![0-9A-Za-z])(?<![0-9][.,]){g}{tail}")


def grade_on_row(
    text: str,
    name: str,
    code: str | None,
    grade: str | None,
    credits: float | None = None,
    term: str | None = None,
) -> bool:
    """True if ``grade`` stands verbatim on a line of ``text`` that holds this course (its code and name),
    outside the row's own name, code, term and credits. An empty grade has nothing to check."""
    if grade is None or not grade.strip():
        return True
    pattern = _grade_pattern(grade.strip())
    return any(
        pattern.search(_without_known_fields(line, name, code, term, credits))
        for line in _lines_for(text, name, code, term)
    )


def credits_on_row(
    text: str, name: str, code: str | None, credits: float | None, term: str | None = None
) -> bool:
    """True if the credits stand on the course's row (as "6", "6.0", "7,5" …); nothing to check when null."""
    if credits is None:
        return True
    lines = [
        _without_known_fields(line, name, code, term, None) for line in _lines_for(text, name, code, term)
    ]
    return any(_number_spans_equal(line, float(credits)) for line in lines)


def _identity(name: str | None, code: str | None, institution: str | None, term: str | None) -> tuple:
    return tuple(fold(v or "") for v in (name, code, institution, term))


def same_course(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Two rows naming the same course (name, code, institution and term, ignoring case)."""
    key_a = _identity(a.get("name"), a.get("code"), a.get("institution"), a.get("term"))
    return key_a == _identity(b.get("name"), b.get("code"), b.get("institution"), b.get("term"))


def is_duplicate(row: dict[str, Any], stored: list[TranscriptCourse]) -> bool:
    key = _identity(row.get("name"), row.get("code"), row.get("institution"), row.get("term"))
    return any(_identity(c.name, c.code, c.institution, c.term) == key for c in stored)


def _heading_columns(
    text: str, name: str, code: str | None, term: str | None, cfg: CoursesConfig
) -> tuple[str | None, str | None] | None:
    """(grade cell, credits cell) of the course's row when it is a tab-table row under a heading row naming a
    grade or credits column (``courses.grade_headings``, ``credit_headings``); None otherwise, and the
    whole-row check applies (DECISIONS #67)."""
    rows = set(_lines_for(text, name, code, term))
    grade_names = {h.casefold() for h in cfg.grade_headings}
    credit_names = {h.casefold() for h in cfg.credit_headings}
    heading: list[str] | None = None
    for line in text.split("\n"):
        cells = [c.strip().casefold() for c in line.split("\t")]
        if "\t" in line and (grade_names & set(cells) or credit_names & set(cells)):
            heading = cells
            continue
        if line in rows and heading is not None and "\t" in line:
            values = line.split("\t")
            if len(values) != len(heading):
                return None
            grade_cell = next((values[i] for i, h in enumerate(heading) if h in grade_names), None)
            credit_cell = next((values[i] for i, h in enumerate(heading) if h in credit_names), None)
            return grade_cell, credit_cell
    return None


def row_problems(
    row: dict[str, Any], text: str, stored: list[TranscriptCourse], cfg: CoursesConfig | None = None
) -> list[str]:
    """Why a row can't be accepted (empty when it can)."""
    if cfg is None:
        from hub.config import load_config

        cfg = load_config().courses
    problems = []
    if not (row.get("name") or "").strip():
        problems.append("The course name is empty")
    elif not _rows_for(text, row["name"], row.get("code")):
        problems.append(COURSE_NOT_FOUND)
    name, code, term = row.get("name") or "", row.get("code"), row.get("term")
    credits = row.get("credits")
    credits = float(credits) if credits not in (None, "") else None
    if term and term.strip() and _rows_for(text, name, code) and not _lines_for(text, name, code, term):
        problems.append(TERM_NOT_FOUND)  # a course taken twice: the term picks the row
    columns = _heading_columns(text, name, code, term, cfg)
    if columns is not None:  # a table with a heading row: grade and credits from their own columns
        grade_cell, credit_cell = columns
        grade = (row.get("grade") or "").strip()
        if grade and (grade_cell is None or grade_cell.strip() != grade):
            problems.append(GRADE_NOT_FOUND)
        if credits is not None and (credit_cell is None or not _number_spans_equal(credit_cell, credits)):
            problems.append(CREDITS_NOT_FOUND)
    else:
        if not grade_on_row(text, name, code, row.get("grade"), credits, term):
            problems.append(GRADE_NOT_FOUND)
        if not credits_on_row(text, name, code, credits, term):
            problems.append(CREDITS_NOT_FOUND)
    if is_duplicate(row, stored):
        problems.append(ALREADY_IMPORTED)
    return problems


def course_id(row: dict[str, Any], taken: set[str]) -> str:
    """A new course id from its name and code, e.g. "corporate-finance-feb12003", never one in ``taken``."""
    base = slugify(" ".join(v for v in (row.get("name"), row.get("code")) if v), "course")
    return unique_slug(base, taken)


def source_name(original: str, today: str, taken: set[str]) -> str:
    """``<original file stem>-<YYYY-MM-DD>.txt`` (or ``pasted-text-…``), never one already in ``sources/``."""
    stem = slugify(original.rsplit(".", 1)[0], "pasted-text") if original else "pasted-text"
    folded = {t.casefold().removesuffix(".txt") for t in taken}
    return unique_slug(f"{stem[:40]}-{today}", folded) + ".txt"


def source_header(original: str, sha256: str) -> str:
    """The comment lines that start ``sources/<name>.txt``: the original file's (cleaned) name and hash."""
    return (
        f"# Original file: {original}\n# sha256: {sha256}\n"
        "# Identifiers removed by Application Hub before storing.\n\n"
    )


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def to_course(row: dict[str, Any], course_id_: str, source: str) -> TranscriptCourse:
    credits = row.get("credits")
    return TranscriptCourse(
        id=course_id_,
        name=row["name"].strip(),
        code=(row.get("code") or "").strip() or None,
        institution=(row.get("institution") or "").strip() or None,
        grade=(row.get("grade") or "").strip() or None,
        credits=float(credits) if credits not in (None, "") else None,
        term=(row.get("term") or "").strip() or None,
        lecturers=[n.strip() for n in row.get("lecturers") or [] if n.strip()],
        description=row.get("description") or "",
        source_document=source,
        confirmed=True,
    )
