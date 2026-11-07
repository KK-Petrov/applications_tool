"""Professors from a document (US-20; docs/02 §9; DECISIONS #44).

Every lecturer of the document's confirmed courses is listed once: names are grouped by the matching key
(surname and initials, case- and diacritic-insensitive), so "Prof. dr. A. van den Berg" and "A. van den Berg"
are one lecturer. Each lecturer shows the owner's grade in each of their courses and whether they were listed
first on any; the list is sorted by the best grade (numeric grades on ``courses.numeric_grade_scale``,
letter grades by ``courses.letter_grade_order``, Pass and unknown last). A lecturer matching an existing
professor (exactly, or on the surname when either has no initials) is offered for linking, never merged.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from hub.models.course import TranscriptCourse
from hub.models.professor import Professor
from hub.programme_form import slugify, unique_slug
from hub.rules import names

if TYPE_CHECKING:
    from hub.config import CoursesConfig, NamesConfig


def grade_score(grade: str | None, cfg: CoursesConfig) -> float:
    """A grade on one scale for sorting (higher is better); -1 for Pass, unknown or none."""
    if not grade:
        return -1.0
    text = grade.strip()
    if re.fullmatch(r"\d+(?:[.,]\d+)?", text):
        value = float(text.replace(",", "."))
        scale = cfg.numeric_grade_scale if value <= cfg.numeric_grade_scale else 100.0
        return min(value / scale, 1.0)
    order = [g.casefold() for g in cfg.letter_grade_order]
    if text.casefold() in order:
        return 1.0 - order.index(text.casefold()) / len(order)
    return -1.0


@dataclass
class Lecturer:
    """One lecturer of the document, with their courses (and the owner's grades)."""

    key: str  # stable form value, e.g. "van-den-berg-a"
    parsed: names.ParsedName  # from the first way the name was written
    written: str
    also_written: list[str] = field(default_factory=list)
    courses: list[tuple[TranscriptCourse, bool]] = field(default_factory=list)  # (course, listed first)
    best: float = -1.0
    matches: list[tuple[Professor, str]] = field(default_factory=list)  # (professor, "exact" | "surname")

    @property
    def listed_first(self) -> bool:
        return any(first for _, first in self.courses)

    @property
    def course_ids(self) -> list[str]:
        return [c.id for c, _ in self.courses]


def group(
    courses: list[TranscriptCourse], names_cfg: NamesConfig, courses_cfg: CoursesConfig
) -> list[Lecturer]:
    """Every lecturer once, with all their courses, sorted by best grade then surname."""
    by_key: dict[tuple, Lecturer] = {}
    for course in courses:
        for position, written in enumerate(course.lecturers):
            parsed = names.parse(written, names_cfg)
            key = parsed.key
            lecturer = by_key.get(key)
            if lecturer is None:
                slug = slugify(f"{parsed.surname} {' '.join(parsed.initials)}", "lecturer")
                lecturer = by_key[key] = Lecturer(key=slug, parsed=parsed, written=written)
            elif written != lecturer.written and written not in lecturer.also_written:
                lecturer.also_written.append(written)
            if course.id not in lecturer.course_ids:
                lecturer.courses.append((course, position == 0))
            lecturer.best = max(lecturer.best, grade_score(course.grade, courses_cfg))
    return sorted(by_key.values(), key=lambda lec: (-lec.best, names.fold(lec.parsed.surname)))


def parsed_professor(professor: Professor, names_cfg: NamesConfig) -> names.ParsedName:
    """A stored professor's name taken apart, with the stored surname winning over the parsed one."""
    parsed = names.parse(professor.name, names_cfg)
    return dataclasses.replace(parsed, surname=professor.surname) if professor.surname else parsed


def match_professors(lecturers: list[Lecturer], professors: list[Professor], names_cfg: NamesConfig) -> None:
    """Fill each lecturer's ``matches`` with the existing professors it may be (offered for linking)."""
    parsed = [(p, parsed_professor(p, names_cfg)) for p in professors]
    for lecturer in lecturers:
        lecturer.matches = [(p, kind) for p, pn in parsed if (kind := names.match(lecturer.parsed, pn))]


def professor_id(surname: str, taken: set[str]) -> str:
    """A new professor id from the whole surname ("van den Berg" → ``van-den-berg``), avoiding ``taken``
    case-insensitively (Windows treats ``Lee.yaml`` and ``lee.yaml`` as one file)."""
    folded = {t.casefold() for t in taken}
    slug = unique_slug(slugify(surname, "professor"), folded)
    taken.add(slug)
    return slug
