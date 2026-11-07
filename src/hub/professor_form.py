"""Add / Edit professor and CSV import (docs/01 §4, docs/03 §4.3–4.4): form values ↔ ``Professor``.

The course description is stored verbatim: never trimmed or re-wrapped. Only the line breaks a browser
adds when it submits a textarea (CRLF) become LF, as for every other multi-line field.
Single-line fields lose outer spaces. Error messages are in words and name the field.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter, ValidationError

from hub.models.common import Email
from hub.models.professor import COURSE_DESCRIPTION_MAX_CHARS, Letter, LetterStatus, Professor, Tone
from hub.programme_form import slugify, unique_slug
from hub.rules import names
from hub.rules.letters import ASKABLE

if TYPE_CHECKING:
    from hub.config import NamesConfig

UNIVERSITIES = ("RSM", "UVA McIntire")  # the form's choices (docs/01 §4); anything else is "Other"
OTHER = "Other"
TONES = {Tone.FORMAL.value: "Formal", Tone.WARM.value: "Slightly warmer"}
SINGLE_LINE = ("name", "title", "email", "course", "grade", "term", "university_other")
DEFAULT_TITLE = "Professor"  # "Dear Professor {surname},"; the form shows it and a blank field means it
LETTER_DATES = ("requested_on", "confirmed_on", "submitted_on", "thanked_on")
LETTER_DATE_LABELS = {
    "requested_on": "Requested on",
    "confirmed_on": "Confirmed on",
    "submitted_on": "Submitted on",
    "thanked_on": "Thanked on",
}
FIELD_LABELS = {
    "name": "Name",
    "title": "Title",
    "email": "Email",
    "university": "University",
    "university_other": "Other institution",
    "course": "Course",
    "course_description": "Course description",
    "grade": "Grade",
    "term": "Term",
    "interaction": "Specific interaction",
    "tone": "Tone",
    "file": "File",
    "course_ids": "Linked courses",
}
CSV_REQUIRED = ("name", "email", "university", "course", "course_description")
CSV_OPTIONAL = ("grade", "term", "interaction")
_EMAIL = TypeAdapter(Email)


def _lf(text: str) -> str:
    """Browser textarea line breaks (CRLF, or a lone CR) as LF; nothing else changes."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


@dataclass
class ProfessorForm:
    """What the form shows: raw text values, ticked programmes, per-letter dates and errors."""

    values: dict[str, str]
    programmes: list[str]
    letter_dates: dict[str, dict[str, str]] = field(default_factory=dict)  # programme → field → YYYY-MM-DD
    course_ids: list[str] = field(default_factory=list)  # linked courses (courses.yaml), Phase 2c
    course_ids_shown: list[str] = field(default_factory=list)  # links the form showed (and could untick)
    errors: list[tuple[str, str]] = field(default_factory=list)  # (field, message)

    def has_error(self, name: str) -> bool:
        return any(f == name for f, _ in self.errors)

    @property
    def university_choice(self) -> str:
        return self.values.get("university", "")


def new_form() -> ProfessorForm:
    values = dict.fromkeys(FIELD_LABELS, "")
    values.update(university=UNIVERSITIES[0], tone=Tone.FORMAL.value, title=DEFAULT_TITLE)
    return ProfessorForm(values, [])


def form_from_professor(p: Professor) -> ProfessorForm:
    known = p.university in UNIVERSITIES
    values = {
        "name": p.name,
        "title": p.title,
        "email": p.email or "",
        "university": p.university if known else OTHER,
        "university_other": "" if known else p.university,
        "course": p.course,
        "course_description": p.course_description,
        "grade": p.grade or "",
        "term": p.term or "",
        "interaction": p.interaction or "",
        "tone": p.tone.value,
    }
    dates = {
        pid: {f: (d.isoformat() if (d := getattr(x, f)) else "") for f in LETTER_DATES}
        for pid, x in p.letters.items()
    }
    return ProfessorForm(values, list(p.letters), dates, list(p.course_ids))


def form_from_request(form: Any) -> ProfessorForm:
    """Read a submitted form (a Starlette FormData)."""
    values: dict[str, str] = {}
    for name in FIELD_LABELS:
        raw = str(form.get(name, "") or "")
        values[name] = raw.strip() if name in SINGLE_LINE else _lf(raw)
    programmes = [str(v) for v in form.getlist("programmes")]
    course_ids = list(dict.fromkeys(str(v) for v in form.getlist("course_ids")))
    shown = list(dict.fromkeys(str(v) for v in form.getlist("course_ids_shown")))
    dates: dict[str, dict[str, str]] = {}
    for key in form:
        if key.startswith("letter-"):
            for f in LETTER_DATES:
                if key.endswith("-" + f):
                    pid = key.removeprefix("letter-").removesuffix("-" + f)
                    dates.setdefault(pid, {})[f] = str(form.get(key, "")).strip()
    return ProfessorForm(values, programmes, dates, course_ids, shown)


# ---- validation ----------------------------------------------------------------------------------


def check_fields(
    values: dict[str, str], names_cfg: NamesConfig | None = None
) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    """Validate the professor fields shared by the form and the CSV import; returns clean values.

    Email is optional (Phase 2c, DECISIONS #44: a professor created from a transcript has none yet; the sheet
    shows "Email missing"). A blank title is taken from a title token in the name ("Dr. J. de Vries" →
    "Dr."), else "Professor" (docs/02 §9).
    """
    errors: list[tuple[str, str]] = []
    if not values.get("name", "").strip():
        errors.append(("name", "is required"))
    email = values.get("email", "").strip()
    if email:
        try:
            _EMAIL.validate_python(email)
        except ValidationError:
            errors.append(("email", "must be an email address, e.g. name@example.org"))
    university = values.get("university", "").strip()
    if university == OTHER:
        university = values.get("university_other", "").strip()
    if not university:
        errors.append(
            ("university_other" if values.get("university") == OTHER else "university", "is required")
        )
    description = values.get("course_description", "")
    if len(description) > COURSE_DESCRIPTION_MAX_CHARS:
        errors.append(
            (
                "course_description",
                f"must be at most {COURSE_DESCRIPTION_MAX_CHARS:,} characters (it has {len(description):,})",
            )
        )
    tone = values.get("tone") or Tone.FORMAL.value
    if tone not in TONES:
        errors.append(("tone", "must be Formal or Slightly warmer"))
    clean = {
        "name": values.get("name", "").strip(),
        "title": values.get("title", "").strip() or _title_from(values.get("name", ""), names_cfg),
        "email": email or None,
        "university": university,
        "course": values.get("course", "").strip(),
        "course_description": description,
        "grade": values.get("grade", "").strip() or None,
        "term": values.get("term", "").strip() or None,
        "interaction": values.get("interaction", "") if values.get("interaction", "").strip() else None,
        "tone": Tone(tone) if tone in TONES else Tone.FORMAL,
    }
    return clean, errors


MULTI_LINE = ("course_description", "interaction")


def _title_from(name: str, names_cfg: NamesConfig | None) -> str:
    """The title a name's title token gives ("Prof. dr. A. van den Berg" → "Professor"), else the default."""
    return names.parse(name, names_cfg).title if names_cfg and name.strip() else DEFAULT_TITLE


def removable(letter: Letter) -> bool:
    """A letter Edit may remove by unticking: To ask / Draft ready with no stamped date (review M1)."""
    return letter.status in ASKABLE and not any(getattr(letter, f) for f in (*LETTER_DATES, "last_contact"))


def _equivalent(name: str, old: Any, new: Any) -> bool:
    """True when the form shows ``old`` exactly as it would submit ``new``: keep the stored value.

    Single-line fields are trimmed by the form, empty text and null look the same, and a browser sends
    CRLF in a textarea; none of that is a change the user made (unchanged saves write nothing, #16c).
    """
    if not isinstance(old, str | None) or not isinstance(new, str | None):
        return old == new
    a, b = old or "", new or ""
    if name in MULTI_LINE:
        return _lf(a) == _lf(b)
    return a.strip() == b.strip()


def message(field_name: str, text: str) -> str:
    """ "Name is required": the field label then the message."""
    return f"{FIELD_LABELS.get(field_name, field_name)} {text}"


def new_id(name: str, taken: set[str]) -> str:
    """A file id from the surname (last word of the name), e.g. "Prof. Rosa Lindqvist" → ``lindqvist``."""
    words = name.split()
    folded = {t.casefold() for t in taken}  # Windows: lee.yaml and Lee.yaml are one file
    slug = unique_slug(slugify(words[-1] if words else name, fallback="professor"), folded)
    taken.add(slug)
    return slug


def _parse_date(text: str) -> date | None:
    return date.fromisoformat(text) if text else None


def build_professor(
    state: ProfessorForm,
    existing: Professor | None,
    taken: set[str],
    programme_ids: dict[str, str],
    names_cfg: NamesConfig | None = None,
    known_courses: set[str] | None = None,
) -> Professor | None:
    """The professor to save, or ``None`` with ``state.errors`` filled. ``programme_ids``: id → short name.

    New professors get a ``not_asked`` ("To ask") letter for each ticked programme. On edit, ticking
    adds such a letter, unticking removes a ``not_asked``/``draft_ready`` letter; a letter with any other
    status is never removed here (the form shows it ticked and disabled). Only fields whose value
    changed are assigned, so an unchanged save writes nothing (DECISIONS #16c, #29b).
    """
    clean, errors = check_fields(state.values, names_cfg)
    linked = [c for c in state.course_ids if known_courses is None or c in known_courses]
    if len(linked) != len(state.course_ids):
        errors.append(("course_ids", "lists a course that is no longer in courses.yaml"))
    ticked = [pid for pid in dict.fromkeys(state.programmes) if pid in programme_ids]
    letters = {k: v.model_copy(deep=True) for k, v in (existing.letters if existing else {}).items()}
    for pid in ticked:
        letters.setdefault(pid, Letter(status=LetterStatus.NOT_ASKED))
    for pid in list(letters):
        typed = any(state.letter_dates.get(pid, {}).values())  # a date typed now keeps the letter
        if pid in programme_ids and pid not in ticked and removable(letters[pid]) and not typed:
            del letters[pid]
    for pid, dates in state.letter_dates.items():
        letter = letters.get(pid)
        if letter is None:
            continue
        changed = False
        for f, text in dates.items():
            try:
                value = _parse_date(text)
            except ValueError:
                label = f"{programme_ids.get(pid, pid)} · {LETTER_DATE_LABELS[f]}"
                errors.append((f"letter-{pid}-{f}", f"{label} must be a date"))
                continue
            if getattr(letter, f) != value:
                setattr(letter, f, value)
                changed = True
        if changed:  # last_contact is the latest stamped date (docs/01 §4)
            letter.last_contact = max(
                (d for d in (getattr(letter, f) for f in LETTER_DATES) if d), default=None
            )
    state.errors = errors
    if errors:
        return None
    if existing is None:
        extra = {"course_ids": linked} if linked else {}
        return Professor(id=new_id(clean["name"], taken), letters=letters, **clean, **extra)
    professor = existing.model_copy(deep=True)
    # A link the form didn't show (its course is missing from, or unreadable in, courses.yaml) can't have
    # been unticked: keep it. Only shown links can be removed (an unchanged Edit wiped them; review).
    shown = set(state.course_ids_shown)
    kept = [i for i in existing.course_ids if i not in shown or i in linked]
    merged = kept + [i for i in linked if i not in kept]
    if professor.course_ids != merged:
        professor.course_ids = merged
    for name, value in clean.items():
        if not _equivalent(name, getattr(professor, name), value):
            setattr(professor, name, value)
    if professor.letters != letters:
        professor.letters = letters
    return professor


# ---- CSV import ----------------------------------------------------------------------------------


@dataclass
class CsvRow:
    number: int  # the spreadsheet row (the header is row 1)
    name: str
    professor: Professor | None
    errors: list[str]


@dataclass
class CsvResult:
    rows: list[CsvRow]
    file_error: str | None = None
    partial: str | None = None  # some rows were saved, then a write failed

    @property
    def valid(self) -> list[Professor]:
        return [r.professor for r in self.rows if r.professor is not None]

    @property
    def invalid(self) -> list[CsvRow]:
        return [r for r in self.rows if r.professor is None]


def decode_csv(raw: bytes) -> str | None:
    """UTF-8 text with any byte-order mark removed (Excel's "CSV UTF-8"); ``None`` if not UTF-8."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def parse_csv(text: str, existing: list[Professor], taken: set[str]) -> CsvResult:
    """Every row checked like the form; rows with errors are listed, the others become professors.

    An email already used by a stored professor, or by an earlier row, is an error ("already exists").
    """
    text = text.removeprefix("\ufeff")
    first = text.split("\n", 1)[0]
    delimiter = ";" if ";" in first and "," not in first else ","  # Dutch Excel saves with semicolons
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
    header = [h.strip().casefold() for h in (reader.fieldnames or [])]
    missing = [c for c in CSV_REQUIRED if c not in header]
    if missing:
        return CsvResult(
            [], f"The file is missing the column{'s' if len(missing) > 1 else ''}: {', '.join(missing)}."
        )
    reader.fieldnames = header
    seen = {p.email.casefold() for p in existing if p.email}  # an empty email is never a duplicate
    ids = set(taken)
    rows: list[CsvRow] = []
    for number, record in enumerate(reader, start=2):
        values = {k: (v or "") for k, v in record.items() if k in CSV_REQUIRED + CSV_OPTIONAL}
        if not any(v.strip() for v in values.values()):
            continue  # a blank line
        for name in MULTI_LINE:
            values[name] = _lf(values.get(name, ""))
        clean, errors = check_fields(values)
        texts = [message(f, m) for f, m in errors]
        if clean["email"] and clean["email"].casefold() in seen:
            texts.append("A professor with this email already exists")
        name = clean["name"]
        if texts:
            rows.append(CsvRow(number, name, None, texts))
            continue
        if clean["email"]:
            seen.add(clean["email"].casefold())
        professor = Professor(id=new_id(name, ids), letters={}, **clean)  # same shape as a form save
        rows.append(CsvRow(number, name, professor, []))
    return CsvResult(rows)
