"""Editing an imported course on /courses/{id}/edit (follow-up before Phase 3; DECISIONS #94; docs/01 §10).

The owner may correct any field of a course. A changed field is listed in ``edited`` for good, so the course
is never presented as "from the transcript" for that field again. No AI call. A value that looks like an
identifier (the same Python check as the import, docs/02 §10) is refused, so nothing under data_dir holds one
(rule 12).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, get_args

from hub.courses import identifiers
from hub.models.course import COURSE_DESCRIPTION_MAX_CHARS, EditableField, TranscriptCourse

if TYPE_CHECKING:
    from hub.config import CoursesConfig

FIELDS: tuple[str, ...] = get_args(EditableField)
LABELS = {
    "name": "Course name",
    "code": "Code",
    "grade": "Grade",
    "credits": "Credits",
    "term": "Term",
    "institution": "Institution",
    "lecturers": "Lecturers",
    "description": "Description",
}
FROM_TRANSCRIPT = ("grade", "credits")  # changing these gets a warning: they came from the transcript


@dataclass
class Outcome:
    """What a submitted form gives: the updated course (None when refused), the fields that changed, the
    errors (field, text) and the values to show again."""

    course: TranscriptCourse | None
    changed: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    values: dict[str, str] = field(default_factory=dict)


def form_values(course: TranscriptCourse) -> dict[str, str]:
    """The course as the form shows it (lecturers one per line; credits as written, e.g. "6" or "7.5")."""
    return {
        "name": course.name,
        "code": course.code or "",
        "grade": course.grade or "",
        "credits": "" if course.credits is None else f"{course.credits:g}",
        "term": course.term or "",
        "institution": course.institution or "",
        "lecturers": "\n".join(course.lecturers),
        "description": course.description,
    }


def transcript_warning(fields: list[str]) -> str:
    """The warning after grade or credits were changed: they came from the transcript."""
    if not fields:
        return ""
    one = len(fields) == 1
    return (
        f" Its {' and '.join(fields)} {'is' if one else 'are'} no longer as your transcript gives "
        f"{'it' if one else 'them'}."
    )


def _lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def apply(course: TranscriptCourse, form: Any, cfg: CoursesConfig, owner: str) -> Outcome:
    """The course with the form's values, or the errors. Single-line fields are trimmed; an empty optional
    field is null; lecturers are one per line (blank lines dropped); the description is kept as typed (line
    breaks as LF)."""
    values = {name: _lf(str(form.get(name, ""))) for name in FIELDS}
    errors: list[tuple[str, str]] = []
    single = {n: " ".join(values[n].split()) for n in ("name", "code", "grade", "term", "institution")}
    if not single["name"]:
        errors.append(("name", "Course name is required."))
    credits: float | None = None
    if values["credits"].strip():
        try:
            credits = float(values["credits"].strip().replace(",", "."))
        except ValueError:
            credits = -1.0
        if not math.isfinite(credits) or credits < 0:
            errors.append(("credits", "Credits must be a number of 0 or more, or empty."))
    lecturers = [" ".join(line.split()) for line in values["lecturers"].split("\n") if line.strip()]
    description = values["description"]
    if len(description) > COURSE_DESCRIPTION_MAX_CHARS:
        errors.append(
            ("description", f"Description is too long (at most {COURSE_DESCRIPTION_MAX_CHARS:,} characters).")
        )
    texts = {**single, "lecturers": "\n".join(lecturers), "description": description}
    for name, text in texts.items():
        if text and identifiers.strip(text, cfg, strict=False, owner=owner) != text:
            errors.append(
                (
                    name,
                    f"{LABELS[name]} looks like a student number, date of birth or national ID number. "
                    "Remove it and save again.",
                )
            )
    if errors:
        return Outcome(None, errors=errors, values=values)
    new = {
        "name": single["name"],
        "code": single["code"] or None,
        "grade": single["grade"] or None,
        "credits": credits,
        "term": single["term"] or None,
        "institution": single["institution"] or None,
        "lecturers": lecturers,
        "description": description,
    }
    changed = [name for name in FIELDS if new[name] != getattr(course, name)]
    edited = [name for name in FIELDS if name in course.edited or name in changed]
    updated = course.model_copy(update={**new, "edited": edited}) if changed else course
    return Outcome(updated, changed=changed, values=values)
