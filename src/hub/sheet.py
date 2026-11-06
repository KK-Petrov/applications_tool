"""The recommender sheet (docs/03 §4.3): one row per professor, one status cell per programme.

Columns are the programmes ordered by primary deadline; every row carries its next action (docs/02 §4).
The sheet never holds a professor's email: only the detail panel shows it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from hub.config import Config
from hub.models.professor import LetterStatus, Professor
from hub.models.programme import Programme
from hub.rules import letters
from hub.rules.programmes import dashboard_order
from hub.views import ProgrammeSummary, summarise

NO_LETTER = "none"  # no key for this programme: "—" (not assigned)
LETTER_STATUS = {
    "not_asked": "To ask",
    "draft_ready": "Draft ready",
    "requested": "Requested",
    "confirmed": "Confirmed",
    "submitted": "Submitted",
    "declined": "Declined",
}
# docs/03 §4.3 colours, by chip class (every chip also has its text)
LETTER_CHIP = {
    "not_asked": "letter-not_asked",
    "draft_ready": "letter-draft_ready",
    "requested": "letter-requested",
    "confirmed": "letter-confirmed",
    "submitted": "letter-submitted",
    "declined": "letter-declined",
}
LETTER_DATE_FIELD = {
    LetterStatus.REQUESTED: "requested_on",
    LetterStatus.CONFIRMED: "confirmed_on",
    LetterStatus.SUBMITTED: "submitted_on",
}


class Filter(StrEnum):
    ALL = "all"
    NEEDS_ACTION = "needs_action"
    WAITING = "waiting"
    DONE = "done"


class Sort(StrEnum):
    NEXT_ACTION = "next_action"
    NAME = "name"
    LAST_CONTACT = "last_contact"


FILTER_LABELS = {
    Filter.ALL: "All professors",
    Filter.NEEDS_ACTION: "Needs action",
    Filter.WAITING: "Waiting on professor",
    Filter.DONE: "Done",
}
SORT_LABELS = {Sort.NEXT_ACTION: "Next action date", Sort.NAME: "Name", Sort.LAST_CONTACT: "Last contact"}


@dataclass(frozen=True)
class Column:
    """One programme: its primary deadline and letter coverage (the totals strip and a column header)."""

    summary: ProgrammeSummary
    in_play: int
    over_ask: bool

    @property
    def programme(self) -> Programme:
        return self.summary.programme


@dataclass(frozen=True)
class Cell:
    programme: Programme
    status: str  # a LetterStatus value, or NO_LETTER

    @property
    def label(self) -> str:
        return LETTER_STATUS.get(self.status, "—")


@dataclass(frozen=True)
class Row:
    professor: Professor
    cells: tuple[Cell, ...]
    action: letters.NextAction | None
    last_contact: date | None
    bucket: letters.Bucket | None


@dataclass(frozen=True)
class Sheet:
    columns: tuple[Column, ...]
    rows: tuple[Row, ...]  # filtered and sorted
    total: int  # professors before search and filter
    q: str
    filter: Filter
    sort: Sort


def parse_filter(value: str | None) -> Filter:
    try:
        return Filter(value or Filter.ALL)
    except ValueError:
        return Filter.ALL


def parse_sort(value: str | None) -> Sort:
    try:
        return Sort(value or Sort.NEXT_ACTION)
    except ValueError:
        return Sort.NEXT_ACTION


def last_contact(professor: Professor) -> date | None:
    """The latest contact date over all letters (``last_contact`` and every stamped date)."""
    days = [
        d
        for x in professor.letters.values()
        for d in (x.last_contact, x.requested_on, x.confirmed_on, x.submitted_on, x.thanked_on)
        if d is not None
    ]
    return max(days, default=None)


def columns(
    programmes: list[Programme], professors: list[Professor], now_utc: datetime, config: Config
) -> tuple[Column, ...]:
    return tuple(
        Column(
            summarise(p, professors, now_utc, config),
            letters.in_play(p.id, professors),
            letters.over_ask(p, professors, config),
        )
        for p in dashboard_order(programmes, now_utc)
    )


def row(
    professor: Professor,
    cols: tuple[Column, ...],
    today: date,
    now_utc: datetime,
    config: Config,
) -> Row:
    by_id = {c.programme.id: c.programme for c in cols}
    action = letters.next_action(professor, by_id, today, now_utc, config)
    cells = tuple(
        Cell(c.programme, x.status.value if (x := professor.letters.get(c.programme.id)) else NO_LETTER)
        for c in cols
    )
    return Row(professor, cells, action, last_contact(professor), letters.bucket(professor, action))


def _matches(r: Row, q: str) -> bool:
    needle = q.casefold()
    return needle in r.professor.name.casefold() or needle in r.professor.course.casefold()


def _sort_key(sort: Sort, today: date):  # noqa: ANN202 (a key function)
    if sort == Sort.NAME:
        return lambda r: (r.professor.resolved_surname.casefold(), r.professor.name.casefold())
    if sort == Sort.LAST_CONTACT:  # most recent first, never contacted last
        return lambda r: (r.last_contact is None, -(r.last_contact or date.min).toordinal(), r.professor.name)
    # next action date: soonest first (priority 1 has no date and counts as today); no action last
    return lambda r: (
        r.action is None,
        (r.action.on or today) if r.action else date.max,
        r.action.priority if r.action else 9,
        r.professor.name.casefold(),
    )


def build(
    programmes: list[Programme],
    professors: list[Professor],
    today: date,
    now_utc: datetime,
    config: Config,
    q: str = "",
    filter: Filter = Filter.ALL,
    sort: Sort = Sort.NEXT_ACTION,
) -> Sheet:
    """The sheet for a search, filter and sort. ``today`` is the display-zone date."""
    cols = columns(programmes, professors, now_utc, config)
    rows = [row(p, cols, today, now_utc, config) for p in professors]
    q = q.strip()
    shown = [
        r
        for r in rows
        if (not q or _matches(r, q))
        and (filter == Filter.ALL or (r.bucket and r.bucket.value == filter.value))
    ]
    shown.sort(key=_sort_key(sort, today))
    return Sheet(cols, tuple(shown), len(rows), q, filter, sort)
