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

import functools
import hashlib
import re
from collections.abc import Sequence
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
NAME_EMPTY = "The course name is empty"
# the field each row problem is about (ui review round 3, F1: the field is described by its problem)
# copied as written (rule 15; ai-output review round 2, F2; DECISIONS #86): these must stand in the document
DESCRIPTION_NOT_FOUND = "Description not found in the document as written"
INSTITUTION_NOT_FOUND = "Institution not found in the document as written"
# the kind of every lecturer problem; the message itself names the lecturer (ui review round 6, W1; #91)
LECTURER_NOT_FOUND = "A lecturer not found in the document as written"
# a returned name that is a proper prefix of the longer name on its row ("Mathematics" for "Mathematics II";
# ai-output review round 3, N3; #91)
NAME_CUT_SHORT = "The course name is cut short: its row has a longer name"
PROBLEM_FIELDS = {
    DESCRIPTION_NOT_FOUND: "description",
    INSTITUTION_NOT_FOUND: "institution",
    LECTURER_NOT_FOUND: "lecturers",
    NAME_EMPTY: "name",
    NAME_CUT_SHORT: "name",
    COURSE_NOT_FOUND: "name",
    TERM_NOT_FOUND: "term",
    GRADE_NOT_FOUND: "grade",
    CREDITS_NOT_FOUND: "credits",
}
ROW_FIELDS = ("name", "code", "institution", "grade", "credits", "term", "lecturers", "description")
_LECTURER_PREFIX = 'Lecturer "'


def lecturer_not_found(name: str) -> str:
    """The problem for one lecturer that doesn't stand in the document as written, naming them."""
    return f'{_LECTURER_PREFIX}{name}" not found in the document as written'


def lecturer_not_whole(name: str) -> str:
    """The problem for a lecturer that is not one whole name: cut off after a particle or an initial, or
    two names joined with ";" (ai-output review round 3, N3)."""
    return f'{_LECTURER_PREFIX}{name}" is not one whole name as written (cut off, or two names joined)'


def kind(problem: str) -> str:
    """The problem's kind: the message itself, except that a lecturer message is ``LECTURER_NOT_FOUND``."""
    return LECTURER_NOT_FOUND if problem.startswith(_LECTURER_PREFIX) else problem


def kinds(problems: list[str]) -> list[str]:
    return [kind(p) for p in problems]


def problem_field(problem: str) -> str | None:
    """The form field a problem is about (it is marked invalid and described by the problem), or None for a
    problem about the whole row."""
    return PROBLEM_FIELDS.get(kind(problem))


def payload(text: str) -> dict[str, Any]:
    """The ``/extract-courses`` input: the stripped text and nothing else (docs/04 §9)."""
    return {"text": text, "institution_hint": None}


def strip_row(row: dict[str, Any], cfg: CoursesConfig, owner: str = "") -> dict[str, Any]:
    """A returned row with every text field stripped of identifiers again (docs/02 §10); ``owner`` is the
    owner's name from settings (a line naming the owner loses its ID-shaped tokens, #93)."""

    def clean(value: Any) -> Any:
        return identifiers.strip(value, cfg, strict=False, owner=owner) if isinstance(value, str) else value

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
            found = [line for line in with_code if name_re is None or name_re.search(line)]
            return found or _wrapped_rows(lines, name, code_re)
    found = [line for line in lines if name_re is not None and name_re.search(line)]
    return found or _wrapped_rows(lines, name, None)


def _wrapped_rows(lines: list[str], name: str, code_re: re.Pattern[str] | None) -> list[str]:
    """The course's row when its name wraps onto the next line in the same column ("Professional Development
    &" / "Mentoring I"; DECISIONS #93): the joined text must be the name exactly, as written. In a table row
    a cell holds the first part and the same cell of the next line holds the rest, alone; on a line without
    tabs the first part is followed only by the row's values (a number or a letter grade) and the next line
    is the rest, alone. A matching refinement, not a looser check: nothing is matched loosely."""
    words = name.split()
    out: list[str] = []
    for i, line in enumerate(lines[:-1]):
        if code_re is not None and not code_re.search(line):
            continue
        following = lines[i + 1]
        for k in range(1, len(words)):
            head, tail = " ".join(words[:k]), " ".join(words[k:])
            if "\t" in line:
                cells, below = line.split("\t"), following.split("\t")
                if any(
                    _flat(cell) == head and c < len(below) and _flat(below[c]) == tail
                    for c, cell in enumerate(cells)
                ):
                    out.append(line)
                    break
                continue
            ends = rf"(?<![\w]){re.escape(head)}(?=\s*$|\s+(?:\d|[A-F][+\-]?(?:\s|$)))"
            if _flat(following) == tail and re.search(ends, _flat(line)):
                out.append(line)
                break
    return out


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


def _alternatives(words: list[str]) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


_YEAR_RANGE = re.compile(r"(?<![0-9])((?:19|20)[0-9]{2})(\s*[-/–]\s*)((?:19|20)?[0-9]{2})(?![0-9A-Za-z])")
_DATE = re.compile(r"(?<![0-9])[0-9]{1,2}[-/.][0-9]{1,2}[-/.](?:[0-9]{4}|[0-9]{2})(?![0-9])")
_YEAR = re.compile(r"(?<![0-9])(?:19|20)[0-9]{2}(?![0-9A-Za-z])(?![.,][0-9])")


def _strip_term_tokens(line: str, cfg: CoursesConfig | None, credits: float | None = None) -> str:
    """``line`` without years, year ranges ("2021-22", "2021/2022": the second year follows the first) and
    "<term word> N" ("block 1", "semester 2", "Q 1"), parts of a term on a row, whatever term the model
    returned (ai-output review round 2, F1; DECISIONS #80). Anchored so they never take a grade
    (data-integrity review round 5, F2; #81): "2021 - 65" is no range (65 may be the grade); a token followed
    by a letter or a decimal is no token ("Block 1A", "semester 7,5"). After a qualifier ("1e semester 7,5",
    "Fall semester 8") N is the grade unless another grade-shaped value, other than the credits, follows it
    on the row ("Fall semester 2 7 4": 2 is the term number; round 6, F4; #82). Known limit: a bare
    "semester 8" is read as the term, so a grade 8 printed right after it is not found (flagged)."""

    def year_range(m: re.Match[str]) -> str:
        first, second = int(m.group(1)), m.group(3)
        follows = int(second) == first + 1 if len(second) == 4 else int(second) == (first + 1) % 100
        return " " if follows else " " + m.group(2) + second

    line = _DATE.sub(
        " ", line
    )  # a date on the row ("12-01-2022", an exam date) is never a grade (round 7, F4)
    line = _YEAR.sub(" ", _YEAR_RANGE.sub(year_range, line))
    if not cfg or not cfg.term_words:
        return line
    word = re.compile(
        # not after a letter or an apostrophe ("Master's 9": round 7, F3)
        rf"(?<![A-Za-z'’])(?P<word>{_alternatives(cfg.term_words)})\.?\s*[0-9]{{1,2}}(?![0-9A-Za-z])(?![.,][0-9])",
        re.IGNORECASE,
    )
    qualifier = re.compile(rf"(?<![0-9A-Za-z])(?:{_alternatives(cfg.term_qualifiers)})\s*$", re.IGNORECASE)
    letters = _alternatives(cfg.letter_grade_order)
    grade_shaped = re.compile(
        rf"(?<![0-9A-Za-z])(?:[0-9]+(?:[.,][0-9]+)?|(?:{letters})(?![+\-]))(?![0-9A-Za-z])(?![.,][0-9])"
    )

    def term_number(m: re.Match[str]) -> str:
        # a qualified term word ("Fall semester") or a one-letter one ("Q 1", "S 2"; round 7, F3) is followed
        # by the grade unless another value comes before the credits
        if len(m.group("word")) > 1 and not qualifier.search(line[: m.start()]):
            return " "
        later = [g.group(0) for g in grade_shaped.finditer(line, m.end())]
        at = (
            next((n for n, v in enumerate(later) if _as_number(v) == float(credits)), None)
            if credits
            else None
        )
        if at is not None:
            later = later[:at]  # only values between N and the credits count (round 7, F4)
        elif later:
            later.pop()  # no credits returned (or not on the row): the last value stands in (02 §10.2)
        return " " if later else m.group(0)

    return word.sub(term_number, line)


def _as_number(value: str) -> float | None:
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return None


def _without_known_fields(
    line: str,
    name: str,
    code: str | None,
    term: str | None,
    credits: float | None,
    cfg: CoursesConfig | None = None,
) -> str:
    """The row with its own name, code, term and one credits value taken out, so the grade can't be "found"
    in them (a grade equal to the credits, a year or a block number; ai-output review). Years and "<term
    word> N" go too when the model returned no term (#80). A real grade equal to the credits is still found:
    the row then holds that number twice."""
    for part in (code, term):
        if part and part.strip():
            line = re.sub(re.escape(part.strip()), " ", line, count=1, flags=re.IGNORECASE)
    if name.split():
        line = _word_pattern(name).sub(" ", line, count=1)
    line = _strip_term_tokens(line, cfg, credits)  # after the name: it may hold a term word or year (#81)
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
    cfg: CoursesConfig | None = None,
) -> bool:
    """True if ``grade`` stands verbatim on a line of ``text`` that holds this course (its code and name),
    outside the row's own name, code, term (and, with ``cfg``, any year or "<term word> N") and credits. An
    empty grade has nothing to check."""
    if grade is None or not grade.strip():
        return True
    pattern = _grade_pattern(grade.strip())
    return any(
        pattern.search(_without_known_fields(line, name, code, term, credits, cfg))
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
    row: dict[str, Any],
    text: str,
    stored: list[TranscriptCourse],
    cfg: CoursesConfig | None = None,
    siblings: Sequence[dict[str, Any]] = (),
) -> list[str]:
    """Why a row can't be accepted (empty when it can). ``siblings`` are the other rows returned from the
    same document: a line holding one of them ends this course's continuation lines (ai-output r3 N1)."""
    if cfg is None:
        from hub.config import load_config

        cfg = load_config().courses
    problems = []
    if not (row.get("name") or "").strip():
        problems.append(NAME_EMPTY)
    elif not _rows_for(text, row["name"], row.get("code")):
        problems.append(COURSE_NOT_FOUND)
    elif _name_cut_short(text, row["name"], row.get("code"), row.get("term"), row.get("grade")):
        problems.append(NAME_CUT_SHORT)
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
        if not grade_on_row(text, name, code, row.get("grade"), credits, term, cfg):
            problems.append(GRADE_NOT_FOUND)
        if not credits_on_row(text, name, code, credits, term):
            problems.append(CREDITS_NOT_FOUND)
    flat = _flat(text)
    if not row.get("institution_entered") and not _as_written(row.get("institution"), flat):
        problems.append(INSTITUTION_NOT_FOUND)  # an owner-entered institution is not from the document (#93)
    # lecturers stand on the course's own row or its continuation lines (a name printed for another course
    # doesn't count; a wrapped name or a "Lecturer:" line under the course does: round 8, F4)
    others = [s for s in siblings if s is not row and not same_course(s, row)]
    on_row = _flat("\n".join(_course_block(text, name, code, term, others))) or flat
    for lecturer in row.get("lecturers") or []:
        if not _whole_name(lecturer):
            problems.append(lecturer_not_whole(_flat(lecturer)))
        elif not _lecturer_as_written(lecturer, on_row):
            problems.append(lecturer_not_found(_flat(lecturer)))
    # so does the description: under any line naming the course (also in a "Course descriptions" appendix,
    # hence without the term), never another course's (ai-output r3 N2)
    described = _flat("\n".join(_course_block(text, name, code, None, others, description=True))) or flat
    if not _as_written(row.get("description"), described) and not _nobody_elses(
        row.get("description") or "", text, flat, row, others
    ):
        problems.append(DESCRIPTION_NOT_FOUND)
    if is_duplicate(row, stored):
        problems.append(ALREADY_IMPORTED)
    return problems


def _nobody_elses(
    description: str, text: str, flat: str, row: dict[str, Any], others: list[dict[str, Any]]
) -> bool:
    """True if a description outside this course's block still stands in the document as written and in no
    other returned row's block: a heading such as "Mathematics I (4 EC)", "1. Mathematics I" or a code alone
    is not one of the course's own lines, but the text is not another course's either (flag on evidence;
    data-integrity r10 F2)."""
    if not _as_written(description, flat):
        return False
    # a line naming this course ("Mathematics I (4 EC)", "FEB11001") ends another course's block here: the
    # last table row's block would otherwise run on into this course's heading and description
    mentions = frozenset(line for line in text.split("\n") if _names_course(line, row))
    for other in others:
        rest = [o for o in others if o is not other] + [row]
        block = _course_block(
            text, other.get("name") or "", other.get("code"), None, rest, description=True, stop_at=mentions
        )
        if block and _as_written(description, _flat("\n".join(block))):
            return False
    return True


def _names_course(line: str, row: dict[str, Any]) -> bool:
    """True if ``line`` names the course: its code or its name as whole words."""
    name, code = (row.get("name") or "").strip(), (row.get("code") or "").strip()
    if code and re.search(rf"(?<![\w]){re.escape(code)}(?![\w])", line):
        return True
    return bool(name) and _word_pattern(name).search(line) is not None


def _flat(text: str) -> str:
    """``text`` with every run of whitespace (line breaks and tabs included) as one space."""
    return " ".join(text.split())


_CODE_START = re.compile(r"^\s*[A-Z]{2,6}\s?\d{3,6}(?![0-9A-Za-z])")  # a line starting with a course code
_INITIAL = re.compile(r"^(?:[A-Z]\.)+$")  # "A.", "J.P."


def _course_block(
    text: str,
    name: str,
    code: str | None,
    term: str | None,
    others: Sequence[dict[str, Any]] = (),
    *,
    description: bool = False,
    stop_at: frozenset[str] = frozenset(),
) -> list[str]:
    """The course's own lines, each with its continuation lines (a wrapped lecturer name, a "Lecturer:" or
    "Description:" line; round 8, F4). A continuation ends at another of the course's own lines, a line
    starting with a course code (not for a description: "GFC 2008 …" may wrap there), another returned
    row's own row or heading (``others``: numbered rows, MIT-style codes, rows without a code; ai-output r3
    N1), or, under a tabbed row, a line with at least as many cells (another table row). A mere mention of
    another course or a code-shaped token ("prerequisite FEB11001 Mathematics I", "room HB 1012") does not
    end it (data-integrity r9 F2, F3). For lecturers a
    blank line ends it; for a ``description`` blank lines don't (paragraphs, a blank line after a heading),
    and a heading that is just the course's name counts as one of its own lines."""
    lines = text.split("\n")
    own = set(_lines_for(text, name, code, term))
    if description and name.strip():
        own |= {line for line in lines if _flat(line).casefold() == _flat(name).casefold()}
    ends: set[str] = set(stop_at)
    for other in others:
        ends |= _own_rows(text, lines, other)
    block: list[str] = []
    for n, line in enumerate(lines):
        if line not in own:
            continue
        block.append(line)
        cells = line.count("\t") + 1 if "\t" in line else None
        for after in lines[n + 1 :]:
            if not after.strip():
                if description:
                    continue
                break
            if (
                after in own
                or after in ends
                or (not description and _CODE_START.match(after))
                or (cells is not None and after.count("\t") + 1 >= cells)
            ):
                break
            block.append(after)
    return block


def _own_rows(text: str, lines: list[str], row: dict[str, Any]) -> set[str]:
    """Another returned row's own lines: a line holding its code and name (or its name, without a code in the
    document) and its grade, so a row and not a prose mention; or its heading (the name alone, or its code
    and name alone)."""
    name, code, grade = (
        (row.get("name") or "").strip(),
        (row.get("code") or "").strip(),
        (row.get("grade") or "").strip(),
    )
    out: set[str] = set()
    if name:
        grade_re = re.compile(rf"(?<![\w.,]){re.escape(grade)}(?![\w]|[.,]\d)") if grade else None
        out |= {
            line for line in _rows_for(text, name, code or None) if grade_re is None or grade_re.search(line)
        }
    headings = {
        _flat(name).casefold(),
        _flat(f"{code} {name}").casefold(),
        _flat(f"{name} {code}").casefold(),
    }
    out |= {line for line in lines if line.strip() and _flat(line).casefold() in headings}
    return out


@functools.cache
def _name_words() -> tuple[frozenset[str], frozenset[str]]:
    """``names.title_tokens`` and ``names.particles`` (config/project.yaml), case-folded, read once."""
    from hub.config import load_config

    names = load_config().names
    return frozenset(t.casefold() for t in names.title_tokens), frozenset(
        p.casefold() for p in names.particles
    )


def _bounded(value: str, flat_text: str) -> list[re.Match[str]]:
    """Every place ``value`` stands in ``flat_text`` as whole words (no letter or digit glued on)."""
    return list(re.finditer(rf"(?<![^\W_]){re.escape(value)}(?![^\W_])", flat_text))


def _whole_name(lecturer: str) -> bool:
    """False for a lecturer that can't be one whole name: two names joined with ";", or a name cut off after
    a particle ("Prof. dr. A. van den") or an initial ("Prof. dr. A."; ai-output review round 3, N3)."""
    name = _flat(lecturer)
    if not name:
        return True
    if ";" in name:
        return False
    last = name.split(" ")[-1]
    _, particles = _name_words()
    return not (last.casefold() in particles or _INITIAL.match(last) or re.fullmatch(r"[A-Z]", last))


def _name_cut_short(text: str, name: str, code: str | None, term: str | None, grade: str | None) -> bool:
    """True if the returned name is a proper prefix of a longer name on its row (ai-output review round 3,
    N3): in a table row, no cell is the name and a cell starts with it and goes on ("Mathematics" in the cell
    "Mathematics II"); on a line without tabs, words (no digit, full stop or semicolon) stand between the
    name and the row's next value or its grade ("FEB11005 Mathematics II 8,5": "II"; a letter grade "A" ends
    the gap), the code aside. A term, a code or a
    lecturer after the name is no continuation. Known limit: an institution's name right after the course
    name on an untabbed row would be read as one."""
    wanted = _flat(name).casefold()
    code_re = re.compile(rf"(?<![\w]){re.escape(code.strip())}(?![\w])") if code and code.strip() else None
    for line in _lines_for(text, name, code, term):
        if "\t" in line:
            cells = [_flat(c).casefold() for c in line.split("\t")]
            if wanted not in cells and any(c.startswith(wanted + " ") for c in cells):
                return True
            continue
        flat = _flat(code_re.sub(" ", line) if code_re else line)
        found = _word_pattern(name).search(flat)
        if found is None:
            continue
        rest = flat[found.end() :]
        if not re.search(r"\d", rest):
            continue  # nothing after the name is a value: the row's values stand elsewhere
        gap = re.match(r"[^\d]*", rest).group(0)
        if grade and grade.strip():
            at = re.search(rf"(?<![\w]){re.escape(grade.strip())}(?![\w+\-])", gap)
            gap = gap[: at.start()] if at else gap
        if _LETTER_RE.search(gap) and not re.search(r"[.;]", gap):
            return True
    return False


_LETTER_RE = re.compile(r"[^\W\d_]")


def _lecturer_as_written(lecturer: str, flat_text: str) -> bool:
    """True if the lecturer stands in the document as written, as whole words, and not only as the tail of a
    longer name: the word before it is no title (also glued, "Prof.dr."), initial or particle ("A. van den
    Berg" where the document says "Prof. dr. A. van den Berg"; "Berg" of "van den Berg"; #86, round 8, F5)."""
    name = _flat(lecturer)
    if not name:
        return True
    titles, particles = _name_words()
    for m in _bounded(name, flat_text):
        before = flat_text[: m.start()].rstrip().split(" ")[-1] if flat_text[: m.start()].strip() else ""
        if before[-1:] in {";", ",", "/", "&", ":"} or not before:
            return True
        parts = {part for part in before.casefold().split(".") if part}
        if parts & titles or _INITIAL.match(before) or before.casefold() in particles:
            continue
        return True
    return False


def _as_written(value: str | None, flat_text: str) -> bool:
    """True if ``value`` is empty or stands in the document exactly as written, as whole words, only
    whitespace and line breaks normalised (rule 15: nothing the model invented or reworded reaches a letter;
    #86, round 8, F5)."""
    return not (value or "").strip() or bool(_bounded(_flat(value), flat_text))


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


def with_owner_institution(row: dict[str, Any], institution: str) -> dict[str, Any]:
    """The row with the owner's own institution entry when the row has none (from the document); a row with
    an institution from the document keeps it (DECISIONS #93)."""
    if not institution.strip() or (row.get("institution") or "").strip():
        return row
    return {**row, "institution": institution.strip(), "institution_entered": True}


def to_course(row: dict[str, Any], course_id_: str, source: str) -> TranscriptCourse:
    credits = row.get("credits")
    return TranscriptCourse(
        id=course_id_,
        name=row["name"].strip(),
        code=(row.get("code") or "").strip() or None,
        institution=(row.get("institution") or "").strip() or None,
        institution_entered=bool(row.get("institution_entered"))
        and bool((row.get("institution") or "").strip()),
        grade=(row.get("grade") or "").strip() or None,
        credits=float(credits) if credits not in (None, "") else None,
        term=(row.get("term") or "").strip() or None,
        lecturers=[n.strip() for n in row.get("lecturers") or [] if n.strip()],
        description=row.get("description") or "",
        source_document=source,
        confirmed=True,
    )
