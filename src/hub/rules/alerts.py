"""Dashboard alert strip (docs/03 §4.1): the single most urgent thing to do, from the reminder engine.

Candidates are the overdue tasks (oldest first) and the upcoming tasks and deadlines (docs/02 §5;
DECISIONS #106 Q2, Q3). "Deadline in N days" notices and interviews are never the alert: they are in
Upcoming reminders. An overdue task beats anything upcoming; otherwise the earliest date wins, ties by rule
order (a letter request first, as DECISIONS #25).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from hub.config import Config
from hub.models.professor import Professor
from hub.models.programme import Programme
from hub.rules import letters
from hub.rules import reminders as engine
from hub.rules.timezones import local_today


class AlertKind(StrEnum):
    LETTERS = "letters"  # request the letters
    DEADLINE = "deadline"  # the deadline itself
    TASK = "task"  # documents, letters check, essays, a follow-up


ALERT_RULES = frozenset({*engine.TASK_RULES, "deadline"})


@dataclass(frozen=True)
class Alert:
    kind: AlertKind
    reminder: engine.Reminder
    programme_id: str
    due: date  # the reminder's date (school-zone date for the deadline rules)
    overdue: bool
    letters_required: int
    letters_assumed: bool
    letters_confirmed: int

    @property
    def text(self) -> str:
        """The reminder's title without the "[Hub] " prefix: "Princeton MFin: send documents (2 not sent)"."""
        return self.reminder.title.removeprefix("[Hub] ")


def most_urgent(
    programmes: list[Programme],
    professors: list[Professor],
    now_utc: datetime,
    config: Config,
    today_tz: str | None = None,
    reminder_tz: str | None = None,
) -> Alert | None:
    """The most urgent alert, or None. ``today_tz`` decides "today" (display zone); ``reminder_tz`` only
    places the events' times, so it doesn't change which alert wins."""
    zone = today_tz or config.time.default_display_tz
    today = local_today(now_utc, zone)
    rtz = reminder_tz or zone
    late = engine.overdue(programmes, professors, rtz, config, today, now_utc)
    soon = [
        r
        for r in engine.reminders(programmes, professors, rtz, config, today, now_utc)
        if r.rule in ALERT_RULES
    ]
    pick = late[0] if late else (soon[0] if soon else None)
    if pick is None:
        return None
    programme = next(p for p in programmes if p.id == pick.programme_id)
    need = letters.required(programme, config.letters.default_required)
    kind = {"letters_request_due": AlertKind.LETTERS, "deadline": AlertKind.DEADLINE}.get(
        pick.rule, AlertKind.TASK
    )
    return Alert(
        kind=kind,
        reminder=pick,
        programme_id=pick.programme_id,
        due=pick.date,
        overdue=pick.overdue,
        letters_required=need.count,
        letters_assumed=need.assumed,
        letters_confirmed=letters.confirmed(programme.id, professors),
    )
