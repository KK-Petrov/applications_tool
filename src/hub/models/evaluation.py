"""An evaluation: ``vN.eval.json`` (docs/01 §6.3; output schema docs/04 §5.2 plus exact checks).

The AI part comments and never rewrites (rule 14); word counts and wrong-school hits are computed in
Python (rule 13) and stored under ``exact_checks``. Written from Phase 4b.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import ConfigDict, Field, field_validator

from hub.models.common import AwareTimestamp, DataModel


class CriterionName(StrEnum):
    STRUCTURE = "structure"
    CONTENT_FIT = "content_fit"
    PROGRAMME_FIT = "programme_fit"
    ACCURACY = "accuracy"


class Rating(StrEnum):
    STRONG = "strong"
    ADEQUATE = "adequate"
    WEAK = "weak"


class Comment(DataModel):
    paragraph: int
    comment: str


class CriterionResult(DataModel):
    name: CriterionName
    rating: Rating
    reason: str = Field(max_length=400)
    comments: list[Comment] = Field(default_factory=list)


class UnusedBiography(DataModel):
    model_config = ConfigDict(extra="allow")  # the JSON schema allows extra keys here

    id: str
    why_it_fits: str


class ExactChecks(DataModel):
    word_count: int = Field(ge=0)
    word_limit: int | None = None
    wrong_school_hits: list[dict[str, Any]] = Field(default_factory=list)  # shape fixed in Phase 4a


class Evaluation(DataModel):
    criteria: list[CriterionResult] = Field(min_length=4, max_length=4)
    unused_biography: list[UnusedBiography] = Field(default_factory=list)
    summary: str = Field(max_length=500)
    exact_checks: ExactChecks
    evaluated_at: AwareTimestamp

    @field_validator("criteria")
    @classmethod
    def _each_criterion_once(cls, items: list[CriterionResult]) -> list[CriterionResult]:
        if sorted(c.name for c in items) != sorted(CriterionName):
            raise ValueError("each of the four rubric criteria must appear exactly once")
        return items
