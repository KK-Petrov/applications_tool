"""Courses imported from a transcript: ``courses.yaml`` (docs/01 §10; DECISIONS #44).

Written only when the user accepts rows in the import preview. Names, codes and grades are kept exactly as
on the transcript (a grade is text: "7,5", "A-", "Pass"); the identifier-stripped text of the document is in
``sources/<source_document>``. Professors link to courses through ``course_ids``.
"""

from __future__ import annotations

from pydantic import Field, field_validator

from hub.models.common import DataModel, Slug, unique_ids

COURSE_DESCRIPTION_MAX_CHARS = 20_000  # docs/01 §10, as a professor's course description (§4)


class TranscriptCourse(DataModel):
    """One course from a transcript, with the owner's grade and the lecturers as written."""

    id: Slug
    name: str = Field(min_length=1)
    code: str | None = None
    institution: str | None = None
    # true when the owner typed the institution in the import preview ("Institution for these courses (your
    # own entry)"): not from the document, so the as-written check does not apply (DECISIONS #93)
    institution_entered: bool = False
    grade: str | None = None
    credits: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    term: str | None = None
    lecturers: list[str] = Field(default_factory=list)  # as written; the first is the listed-first lecturer
    description: str = Field(default="", max_length=COURSE_DESCRIPTION_MAX_CHARS)  # verbatim
    source_document: str | None = None  # a file in sources/, or null for a course added by hand
    confirmed: bool = False  # true when accepted in the import preview


class Courses(DataModel):
    """The whole ``courses.yaml``."""

    courses: list[TranscriptCourse] = Field(default_factory=list)

    @field_validator("courses")
    @classmethod
    def _ids_unique(cls, items: list[TranscriptCourse]) -> list[TranscriptCourse]:
        return unique_ids(items)
