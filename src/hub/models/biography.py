"""The biography bank: ``biography.yaml`` (docs/01 §5), the only source of facts for generated text."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, field_validator

from hub.models.common import DataModel, Slug, unique_ids


class BiographyCategory(StrEnum):
    EDUCATION = "education"
    TEST = "test"
    EXPERIENCE = "experience"
    HONOUR = "honour"
    SKILL = "skill"
    GOAL = "goal"
    OTHER = "other"


class EntryState(StrEnum):
    ACTUAL = "actual"
    ANTICIPATED = "anticipated"  # not yet achieved; may only be mentioned as expected


class BiographyEntry(DataModel):
    id: Slug
    category: BiographyCategory
    title: str
    detail: str | None = None
    tags: list[str] = Field(default_factory=list)
    state: EntryState = EntryState.ACTUAL
    dates: str | None = None  # free text, e.g. "Jun–Aug 2025"


class Biography(DataModel):
    entries: list[BiographyEntry] = Field(default_factory=list)

    @field_validator("entries")
    @classmethod
    def _ids_unique(cls, items: list[BiographyEntry]) -> list[BiographyEntry]:
        return unique_ids(items)
