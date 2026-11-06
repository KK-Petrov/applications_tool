"""Letter coverage (docs/02 §4). Phase 1 needs the counts for the dashboard; Phase 2a adds next actions.

``required(p)`` is the programme's ``letters_required`` or the config default, flagged "assumed";
``confirmed(p)`` counts letters ``confirmed`` or ``submitted``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from hub.models.professor import LetterStatus, Professor
from hub.models.programme import Programme
from hub.rules.programmes import primary_deadline


@dataclass(frozen=True)
class Required:
    count: int
    assumed: bool  # True when the programme does not state it (config letters.default_required)


def required(programme: Programme, default_required: int) -> Required:
    if programme.letters_required is None:
        return Required(default_required, True)
    return Required(programme.letters_required, False)


def _count(programme_id: str, professors: list[Professor], statuses: set[LetterStatus]) -> int:
    return sum(1 for prof in professors if (x := prof.letters.get(programme_id)) and x.status in statuses)


def confirmed(programme_id: str, professors: list[Professor]) -> int:
    """Letters to the programme with status confirmed or submitted."""
    return _count(programme_id, professors, {LetterStatus.CONFIRMED, LetterStatus.SUBMITTED})


def requested(programme_id: str, professors: list[Professor]) -> int:
    """Letters to the programme with status requested (asked, no answer yet)."""
    return _count(programme_id, professors, {LetterStatus.REQUESTED})


def request_due(programme: Programme, now_utc: datetime, request_days: int) -> date:
    """Primary deadline's local date (school zone) minus ``reminders.letters_request_days``."""
    return primary_deadline(programme, now_utc).due_local.date() - timedelta(days=request_days)
