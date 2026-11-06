"""Interim dashboard alert (docs/03 §4.1): the single most urgent item from deadlines and letters.

Phase 3 replaces this with the reminder engine. Candidates per programme that is not submitted and
whose primary deadline has not passed: "request letters" (dated request_due, while requested +
confirmed < required) and the deadline itself (dated by its local date). The earliest date wins; on a
tie the letter request wins, because it must happen first (DECISIONS #25).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from hub.config import Config
from hub.models.professor import Professor
from hub.models.programme import Programme, ProgrammeStatus
from hub.rules import letters
from hub.rules.programmes import deadline_utc, primary_deadline
from hub.rules.timezones import local_today


class AlertKind(StrEnum):
    LETTERS = "letters"
    DEADLINE = "deadline"


@dataclass(frozen=True)
class Alert:
    kind: AlertKind
    programme_id: str
    due: date  # the date that decides urgency (request_due, or the deadline's local date)
    overdue: bool
    letters_required: int
    letters_assumed: bool
    letters_confirmed: int


def most_urgent(
    programmes: list[Programme],
    professors: list[Professor],
    now_utc: datetime,
    config: Config,
    today_tz: str | None = None,
) -> Alert | None:
    """The most urgent alert, or None when nothing is open. ``today_tz`` decides "today" (display zone)."""
    today = local_today(now_utc, today_tz or config.time.default_display_tz)
    candidates: list[tuple[date, int, Alert]] = []
    for p in programmes:
        if p.status == ProgrammeStatus.SUBMITTED:
            continue
        deadline = primary_deadline(p, now_utc)
        if deadline_utc(deadline) < now_utc:
            continue
        need = letters.required(p, config.letters.default_required)
        have = letters.confirmed(p.id, professors)
        asked = letters.requested(p.id, professors)
        common = {
            "programme_id": p.id,
            "letters_required": need.count,
            "letters_assumed": need.assumed,
            "letters_confirmed": have,
        }
        if have + asked < need.count:
            due = letters.request_due(p, now_utc, config.reminders.letters_request_days)
            candidates.append((due, 0, Alert(AlertKind.LETTERS, due=due, overdue=due < today, **common)))
        due = deadline.due_local.date()
        candidates.append((due, 1, Alert(AlertKind.DEADLINE, due=due, overdue=False, **common)))
    if not candidates:
        return None
    return min(candidates, key=lambda c: (c[0], c[1]))[2]
