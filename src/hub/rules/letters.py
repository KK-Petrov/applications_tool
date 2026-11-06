"""Letter coverage and next actions (docs/02 §4): the dashboard counts and the recommender sheet.

``required(p)`` is the programme's ``letters_required`` or the config default, flagged "assumed";
``confirmed(p)`` counts letters ``confirmed`` or ``submitted``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from hub.config import Config
from hub.models.professor import Letter, LetterStatus, Professor
from hub.models.programme import Programme
from hub.rules.programmes import primary_deadline
from hub.rules.timezones import format_date


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


def in_play(programme_id: str, professors: list[Professor]) -> int:
    """Letters asked and not declined: requested + confirmed + submitted."""
    return _count(
        programme_id,
        professors,
        {LetterStatus.REQUESTED, LetterStatus.CONFIRMED, LetterStatus.SUBMITTED},
    )


def over_ask(programme: Programme, professors: list[Professor], config: Config) -> bool:
    """More letters in play than required + ``letters.max_extra``: the user is asking too many people."""
    need = required(programme, config.letters.default_required).count
    return in_play(programme.id, professors) > need + config.letters.max_extra


# ---- next action (docs/02 §4) --------------------------------------------------------------------


@dataclass(frozen=True)
class NextAction:
    """The single most urgent thing to do for a professor.

    ``on`` is the date the action is (or becomes) due, used for sorting and for ties: the request-due
    date (2, 4), the follow-up date (3, 7), the submission-check date (5, 8) or the submission date (6).
    """

    priority: int
    text: str
    urgent: bool
    programme_id: str | None
    on: date | None


class Bucket(StrEnum):
    """The sheet's filters: Needs action = priorities 1–6, Waiting on professor = 7–8, Done."""

    NEEDS_ACTION = "needs_action"
    WAITING = "waiting"
    DONE = "done"


ASKABLE = {LetterStatus.NOT_ASKED, LetterStatus.DRAFT_READY}


def _letter_action(
    letter: Letter, programme: Programme, today: date, now_utc: datetime, config: Config
) -> NextAction | None:
    """Priorities 2–8 for one letter (priority 1 is about the professor, not one letter)."""
    fmt = lambda d: format_date(d, config.time)  # noqa: E731
    pid = programme.id
    due = request_due(programme, now_utc, config.reminders.letters_request_days)
    deadline_day = primary_deadline(programme, now_utc).due_local.date()
    check_from = deadline_day - timedelta(days=config.reminders.letters_check_days)
    status = letter.status
    if status in ASKABLE:
        if today > due:
            return NextAction(2, f"Send request (overdue since {fmt(due)})", True, pid, due)
        soon = (due - today).days <= config.letters.request_soon_days
        return NextAction(4, f"Send request by {fmt(due)}", soon, pid, due)
    if status == LetterStatus.REQUESTED:
        asked = letter.requested_on
        follow_up = asked + timedelta(days=config.letters.follow_up_days) if asked else None
        if follow_up is not None and today >= follow_up:
            return NextAction(3, f"Follow up (asked {fmt(asked)})", True, pid, follow_up)
        return NextAction(7, "Waiting for reply", False, pid, follow_up)
    if status == LetterStatus.CONFIRMED:
        if today >= check_from:
            return NextAction(5, f"Check submission for {programme.short_name}", True, pid, check_from)
        return NextAction(8, "Waiting for submission", False, pid, check_from)
    if status == LetterStatus.SUBMITTED and letter.thanked_on is None:
        return NextAction(6, "Send thank-you note", False, pid, letter.submitted_on)
    return None  # declined, or submitted and thanked


def next_action(
    professor: Professor,
    programmes: Mapping[str, Programme],
    today: date,
    now_utc: datetime,
    config: Config,
) -> NextAction | None:
    """Evaluate every letter, take the highest priority; ties go to the earliest date (docs/02 §4).

    ``today`` is the display-zone date. Letters to a programme that is not loaded (deleted or
    unreadable file) are ignored: their dates cannot be computed.
    """
    known = {pid: letter for pid, letter in professor.letters.items() if pid in programmes}
    if known and not (professor.course.strip() and professor.course_description.strip()):
        return NextAction(1, "Add course details", False, None, None)
    actions = [
        a
        for pid, letter in known.items()
        if (a := _letter_action(letter, programmes[pid], today, now_utc, config)) is not None
    ]
    if not actions:
        return None
    return min(actions, key=lambda a: (a.priority, a.on or date.max, a.programme_id or ""))


def bucket(professor: Professor, action: NextAction | None) -> Bucket | None:
    """Which sheet filter the professor falls under; ``None`` for a professor with no letters."""
    if action is not None:
        return Bucket.NEEDS_ACTION if action.priority <= 6 else Bucket.WAITING
    finished = all(
        x.status == LetterStatus.DECLINED or (x.status == LetterStatus.SUBMITTED and x.thanked_on)
        for x in professor.letters.values()
    )
    return Bucket.DONE if professor.letters and finished else None


STAMPED_DATE = {
    LetterStatus.REQUESTED: "requested_on",
    LetterStatus.CONFIRMED: "confirmed_on",
    LetterStatus.SUBMITTED: "submitted_on",
}


def set_status(letter: Letter | None, status: LetterStatus, today: date) -> Letter:
    """The letter after the user chooses ``status`` (docs/01 §4).

    Requested, confirmed and submitted stamp their date with ``today`` (the display-zone date) and
    ``last_contact`` becomes the latest stamped date. Dates stamped earlier are kept when the status
    moves back (they stay editable in Edit). A new letter is created for a programme with no letter.
    """
    out = letter.model_copy(deep=True) if letter else Letter(status=status)
    out.status = status
    stamp = STAMPED_DATE.get(status)
    if stamp:
        setattr(out, stamp, today)
        out.last_contact = max(d for d in (out.last_contact, today) if d is not None)
    return out
