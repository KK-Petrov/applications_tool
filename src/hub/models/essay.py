"""Essay and CV metadata: ``essays/<programme_id>/<essay_id>/meta.yaml`` and ``cv/*/meta.yaml``.

docs/01 §6–7. Folder loaders arrive in Phase 4a; the models are fixed now so every data file has a schema.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, ValidationInfo, field_validator

from hub.models.common import DataModel, Slug, unique_ids


class Version(DataModel):
    """One immutable saved draft ``v<n>.md``."""

    n: int = Field(ge=1)
    saved_at: datetime
    words: int = Field(ge=0)


def _check_versions(versions: list[Version], current: int | None) -> list[Version]:
    unique_ids(versions, "n")
    if versions and current is not None and current not in {v.n for v in versions}:
        raise ValueError(f"current_version {current} has no entry in versions")
    return versions


class EssayMeta(DataModel):
    programme_id: Slug
    essay_id: Slug  # matches programmes/<id>.yaml → essays[].id
    title: str
    prompt: str | None = None
    word_limit: int | None = Field(default=None, ge=1)
    current_version: int = Field(ge=0)
    versions: list[Version] = Field(default_factory=list)

    @field_validator("versions")
    @classmethod
    def _versions_consistent(cls, versions: list[Version], info: ValidationInfo) -> list[Version]:
        return _check_versions(versions, info.data.get("current_version"))


class CvKind(StrEnum):
    BASE = "base"
    TAILORED = "tailored"


class CvMeta(DataModel):
    kind: CvKind
    programme_id: Slug | None = None  # null for the base CV
    based_on: int | None = Field(default=None, ge=1)  # base version a tailored CV started from
    current_version: int = Field(ge=0)
    versions: list[Version] = Field(default_factory=list)

    @field_validator("programme_id")
    @classmethod
    def _programme_matches_kind(cls, value: str | None, info: ValidationInfo) -> str | None:
        kind = info.data.get("kind")
        if kind == CvKind.BASE and value is not None:
            raise ValueError("the base CV has no programme_id")
        if kind == CvKind.TAILORED and value is None:
            raise ValueError("a tailored CV needs a programme_id")
        return value

    @field_validator("versions")
    @classmethod
    def _versions_consistent(cls, versions: list[Version], info: ValidationInfo) -> list[Version]:
        return _check_versions(versions, info.data.get("current_version"))
