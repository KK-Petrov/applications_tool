"""A recommender: ``professors/<id>.yaml`` (docs/01 §4)."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import Field

from hub.models.common import AwareTimestamp, DataModel, Email, Slug

COURSE_DESCRIPTION_MAX_CHARS = 20_000  # docs/01 §4


class LetterStatus(StrEnum):
    NOT_ASKED = "not_asked"
    DRAFT_READY = "draft_ready"
    REQUESTED = "requested"
    CONFIRMED = "confirmed"
    SUBMITTED = "submitted"
    DECLINED = "declined"


class Tone(StrEnum):
    FORMAL = "formal"
    WARM = "warm"


class Letter(DataModel):
    """One professor × one programme. Setting a status stamps the matching date (Phase 2a)."""

    status: LetterStatus
    requested_on: date | None = None
    confirmed_on: date | None = None
    submitted_on: date | None = None
    thanked_on: date | None = None
    last_contact: date | None = None


class GeneratedEmail(DataModel):
    """A request-email draft. Copy-paste only: the app never sends it (rule 9)."""

    generated_at: AwareTimestamp
    programmes: list[Slug]
    subject: str
    body: str
    # shown with the draft; unverified-deadline warnings first (DECISIONS #48)
    warnings: list[str] = Field(default_factory=list)
    marked_sent_on: date | None = None


class Professor(DataModel):
    """A recommender. The email is stored for the generator and never shown in the sheet."""

    id: Slug
    name: str
    surname: str | None = None  # default: last token of ``name``
    title: str = "Professor"  # "Dear {title} {surname},"; e.g. "Dr." (Phase 2b, DECISIONS #48)
    email: Email
    university: str
    course: str
    course_description: str = Field(default="", max_length=COURSE_DESCRIPTION_MAX_CHARS)  # verbatim
    grade: str | None = None
    term: str | None = None
    interaction: str | None = None
    tone: Tone = Tone.FORMAL
    letters: dict[Slug, Letter] = Field(default_factory=dict)  # absent key = not asked for that programme
    emails: list[GeneratedEmail] = Field(default_factory=list)  # newest last
    notes: str | None = None

    @property
    def resolved_surname(self) -> str:
        """Surname for "Dear Professor {surname},": the override, else the last token of the name."""
        return self.surname or self.name.split()[-1]
