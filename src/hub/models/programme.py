"""One degree programme at one school: ``programmes/<id>.yaml`` (docs/01 §3)."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import Field, ValidationInfo, field_validator

from hub.models.common import DataModel, IanaZone, LocalDateTime, Slug, Url, Verification, unique_ids


class ProgrammeStatus(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"


class DocumentStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    REQUESTED = "requested"
    SENT = "sent"
    RECEIVED = "received"
    WAIVED = "waived"


class InterviewKind(StrEnum):
    INTERVIEW = "interview"
    ASSESSMENT = "assessment"


class InterviewStatus(StrEnum):
    NOT_INVITED = "not_invited"
    INVITED = "invited"
    SCHEDULED = "scheduled"
    DONE = "done"


class Source(DataModel):
    """A page the programme data was taken from, with the date it was last checked."""

    url: Url
    title: str
    checked: date | None = None


class Deadline(DataModel):
    """A deadline: local wall-clock time + IANA zone + source URL (rule 10: none of the three is optional)."""

    id: Slug
    label: str
    due_local: LocalDateTime
    tz: IanaZone
    time_known: bool = (
        True  # False → time assumed (config time.assumed_deadline_time), shown "(time to verify)"
    )
    verification: Verification = Verification.UNVERIFIED
    source_url: Url


class Requirement(DataModel):
    id: Slug
    label: str
    note: str | None = None
    done: bool = False


class Step(DataModel):
    order: int = Field(ge=1)
    label: str
    note: str | None = None
    done: bool = False


class Document(DataModel):
    """A document or score report and how it reaches the school."""

    id: Slug
    label: str
    method: str | None = None
    status: DocumentStatus = DocumentStatus.NOT_REQUESTED
    updated: date | None = None
    note: str | None = None


class Interview(DataModel):
    """An interview or assessment; ``estimated`` marks a date inferred from last cycle."""

    id: Slug
    kind: InterviewKind
    label: str
    start_local: LocalDateTime
    tz: IanaZone
    estimated: bool = False
    status: InterviewStatus = InterviewStatus.NOT_INVITED
    notes: str | None = None


class EssayPrompt(DataModel):
    id: Slug
    title: str
    prompt: str | None = None
    word_limit: int | None = Field(default=None, ge=1)


class Course(DataModel):
    name: str
    description: str | None = None


class Curriculum(DataModel):
    summary: str | None = None
    courses: list[Course] = Field(default_factory=list)


class AiPolicy(DataModel):
    text: str | None = None
    source_url: Url | None = None


class Fee(DataModel):
    amount: float | None = Field(default=None, ge=0)
    currency: str = "USD"
    note: str | None = None


class Programme(DataModel):
    """Everything an application needs. Unverified until the user clicks Mark verified (rule 11)."""

    id: Slug
    name: str
    short_name: str
    school: str
    unit: str | None = None
    aliases: list[str] = Field(default_factory=list)  # proper nouns for the wrong-school check (docs/02 §8)
    status: ProgrammeStatus = ProgrammeStatus.NOT_STARTED
    verification: Verification = Verification.UNVERIFIED
    verification_note: str | None = None  # badge reason (docs/02 §3); null when verified
    verified_on: date | None = None
    portal_url: Url | None = None
    sources: list[Source] = Field(default_factory=list, validate_default=True)  # checked even when absent
    deadlines: list[Deadline] = Field(min_length=1)
    letters_required: int | None = Field(
        default=None, ge=0
    )  # null → config letters.default_required, "assumed"
    letters_note: str | None = None
    requirements: list[Requirement] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    documents: list[Document] = Field(default_factory=list)
    interviews: list[Interview] = Field(default_factory=list)
    essays: list[EssayPrompt] = Field(default_factory=list)
    curriculum: Curriculum = Field(default_factory=Curriculum)
    ai_policy: AiPolicy = Field(default_factory=AiPolicy)
    fee: Fee = Field(default_factory=Fee)

    @field_validator("sources")
    @classmethod
    def _verified_needs_a_source(cls, sources: list[Source], info: ValidationInfo) -> list[Source]:
        if info.data.get("verification") == Verification.VERIFIED and not sources:
            raise ValueError("a verified programme needs at least one source")
        return sources

    @field_validator("deadlines", "requirements", "documents", "interviews", "essays")
    @classmethod
    def _ids_unique(cls, items: list) -> list:
        return unique_ids(items)

    @field_validator("steps")
    @classmethod
    def _orders_unique(cls, items: list[Step]) -> list[Step]:
        return unique_ids(items, "order")
