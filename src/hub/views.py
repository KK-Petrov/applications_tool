"""View models: what the dashboard and programme pages show, computed from the data and the rules.

Templates only lay these out. Every time on a page is an aware UTC instant here and is turned into
text by the ``when`` / ``school_time`` filters or the ``_time.html`` macros (C-1-01).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hub.config import Config
from hub.models.common import Verification
from hub.models.professor import Professor
from hub.models.programme import Deadline, Interview, Programme
from hub.rules import alerts, letters
from hub.rules.countdown import Countdown, countdown
from hub.rules.programmes import (
    Badge,
    badge,
    dashboard_order,
    documents_progress,
    primary_deadline,
    requirements_progress,
)
from hub.rules.timezones import to_instant

PROGRAMME_STATUS = {"not_started": "Not started", "in_progress": "In progress", "submitted": "Submitted"}
DOCUMENT_STATUS = {
    "not_requested": "Not requested",
    "requested": "Requested",
    "sent": "Sent",
    "received": "Received",
    "waived": "Waived",
}
INTERVIEW_KIND = {"interview": "Interview", "assessment": "Assessment"}
INTERVIEW_STATUS = {
    "not_invited": "Not invited",
    "invited": "Invited",
    "scheduled": "Scheduled",
    "done": "Done",
}
LABELS = {
    "programme_status": PROGRAMME_STATUS,
    "document_status": DOCUMENT_STATUS,
    "interview_kind": INTERVIEW_KIND,
    "interview_status": INTERVIEW_STATUS,
}
VERIFICATION_REASONS = ("Last cycle's date", "Third-party source", "Researched, not checked")  # docs/02 §3


@dataclass(frozen=True)
class DeadlineView:
    """One deadline ready for the provenance macro: instant, school zone, source and flags."""

    key: str  # "<programme_id>:<deadline_id>", used by the provenance test
    label: str
    utc: datetime
    tz: str
    time_known: bool
    adjusted: bool  # fell in a DST gap and was moved forward (docs/02 §1)
    unverified: bool
    source_url: str

    @classmethod
    def of(cls, programme: Programme, deadline: Deadline) -> DeadlineView:
        instant = to_instant(deadline.due_local, deadline.tz)
        return cls(
            key=f"{programme.id}:{deadline.id}",
            label=deadline.label,
            utc=instant.utc,
            tz=deadline.tz,
            time_known=deadline.time_known,
            adjusted=instant.adjusted,
            unverified=deadline.verification == Verification.UNVERIFIED,
            source_url=deadline.source_url,
        )


@dataclass(frozen=True)
class InterviewView:
    interview: Interview
    utc: datetime
    tz: str
    adjusted: bool

    @classmethod
    def of(cls, interview: Interview) -> InterviewView:
        instant = to_instant(interview.start_local, interview.tz)
        return cls(interview, instant.utc, interview.tz, instant.adjusted)


@dataclass(frozen=True)
class ProgrammeSummary:
    """A programme with everything the dashboard row, tiles and programme header need."""

    programme: Programme
    deadline: DeadlineView  # the primary deadline
    countdown: Countdown
    letters_required: letters.Required
    letters_confirmed: int
    requirements: tuple[int, int]
    documents: tuple[int, int]
    badge: Badge


def summarise(
    programme: Programme, professors: list[Professor], now_utc: datetime, config: Config
) -> ProgrammeSummary:
    deadline = DeadlineView.of(programme, primary_deadline(programme, now_utc))
    return ProgrammeSummary(
        programme=programme,
        deadline=deadline,
        countdown=countdown(deadline.utc, now_utc, config.urgency.orange_days, config.urgency.red_days),
        letters_required=letters.required(programme, config.letters.default_required),
        letters_confirmed=letters.confirmed(programme.id, professors),
        requirements=requirements_progress(programme),
        documents=documents_progress(programme),
        badge=badge(programme),
    )


@dataclass(frozen=True)
class Tiles:
    next_deadline: ProgrammeSummary | None
    tracked: int
    unverified: int
    letters_confirmed: int
    letters_required: int
    letters_assumed: bool
    essays_evaluated: int  # evaluations arrive in Phase 4b
    essays_total: int


@dataclass(frozen=True)
class Dashboard:
    rows: list[ProgrammeSummary]
    alert: alerts.Alert | None
    alert_programme: ProgrammeSummary | None
    tiles: Tiles


def dashboard(
    programmes: list[Programme],
    professors: list[Professor],
    now_utc: datetime,
    config: Config,
    display_tz: str,
) -> Dashboard:
    rows = [summarise(p, professors, now_utc, config) for p in dashboard_order(programmes, now_utc)]
    alert = alerts.most_urgent(programmes, professors, now_utc, config, today_tz=display_tz)
    by_id = {r.programme.id: r for r in rows}
    upcoming = [r for r in rows if not r.countdown.passed and r.programme.status != "submitted"]
    tiles = Tiles(
        next_deadline=upcoming[0] if upcoming else None,
        tracked=len(rows),
        unverified=sum(r.badge.unverified for r in rows),
        letters_confirmed=sum(min(r.letters_confirmed, r.letters_required.count) for r in rows),
        letters_required=sum(r.letters_required.count for r in rows),
        letters_assumed=any(r.letters_required.assumed for r in rows),
        essays_evaluated=0,
        essays_total=sum(len(r.programme.essays) for r in rows),
    )
    return Dashboard(rows, alert, by_id.get(alert.programme_id) if alert else None, tiles)
