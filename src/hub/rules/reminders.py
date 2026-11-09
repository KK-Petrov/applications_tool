"""The reminder engine (docs/02 §5; C-3-01): what should be on the owner's calendar, as data.

Pure: from the programmes, professors, ``settings.reminder_tz`` and "today" it returns every reminder dated
today or later. Dates of the deadline rules are counted back from the **primary** deadline's local date in
the school zone (D; docs/02 §5.1, DECISIONS #106 Q9); offsets come from ``config.reminders``. Rule
reminders are timed events at ``reminders.time_of_day`` in the reminder zone; the deadline and interviews are
at their own instant in their own zone (§5.2). When a programme is submitted only its interviews remain.

``overdue`` uses the same rules without the date floor and returns the past **tasks** whose condition still
holds while the deadline has not passed (DECISIONS #106 Q2): they go to the dashboard alert and the digest's
Overdue section, never to the calendar. Deadline alerts and interviews are notices, so they are never overdue.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from hub.config import Config
from hub.models.common import Verification
from hub.models.professor import LetterStatus, Professor
from hub.models.programme import (
    Deadline,
    DocumentStatus,
    InterviewStatus,
    Programme,
    ProgrammeStatus,
)
from hub.rules import letters
from hub.rules.programmes import NOT_CHECKED_REASON, primary_deadline
from hub.rules.timezones import to_instant

# Rule order breaks ties between reminders on the same date (a letter request first, as DECISIONS #25).
RULE_ORDER = (
    "letters_request_due",
    "documents_due",
    "letters_check",
    "essay_final",
    "letter_follow_up",
    "deadline_alert_14",
    "deadline_alert_7",
    "deadline_alert_2",
    "deadline_alert_1",
    "deadline",
    "interview_eve",
    "interview",
)
TASK_RULES = frozenset(
    {"letters_request_due", "documents_due", "letters_check", "essay_final", "letter_follow_up"}
)
CATEGORY = {
    "letters_request_due": "letters",
    "letters_check": "letters",
    "letter_follow_up": "letters",
    "documents_due": "documents",
    "essay_final": "essays",
    "deadline": "deadline",
    "interview": "interview",
    "interview_eve": "interview",
}
OPEN_DOCUMENTS = frozenset({DocumentStatus.NOT_REQUESTED, DocumentStatus.REQUESTED})


@dataclass(frozen=True)
class Reminder:
    """One calendar entry: rule × programme × subject (docs/02 §5.3)."""

    id: str  # "<rule>:<programme_id>" plus ":<professor or interview id>"
    rule: str
    programme_id: str
    subject_id: str | None
    category: str  # letters · documents · essays · deadline · interview (the dashboard chip)
    date: date  # the date the rule gives (school-zone date for the deadline rules)
    start: datetime  # aware, in ``tz``
    tz: str
    minutes: int
    popups: tuple[int, ...]  # minutes before the start
    title: str
    description: str
    timed_at_own_instant: bool = False  # the deadline or an interview (shown with its time and zone)
    overdue: bool = False

    @property
    def sort_key(self) -> tuple[date, int, str]:
        return self.date, RULE_ORDER.index(self.rule), self.id


def app_url(config: Config) -> str:
    """The local app's address (``config.app``), linked from every event and the digest."""
    return f"http://{config.app.host}:{config.app.port}"


def _unverified(programme: Programme, deadline: Deadline) -> str | None:
    """The reason the deadline is unverified (rule 11), or None when programme and deadline are verified."""
    if programme.verification == Verification.UNVERIFIED:
        return programme.verification_note or NOT_CHECKED_REASON
    if deadline.verification == Verification.UNVERIFIED:
        return "Deadline changed, not checked"
    return None


def _when(local: datetime, tz: str) -> str:
    return f"{local:%d %b %Y, %H:%M} ({tz})"


class _Builder:
    """Builds one programme's reminders (shared facts in one place)."""

    def __init__(
        self, p: Programme, professors: list[Professor], reminder_tz: str, config: Config, now_utc: datetime
    ):
        self.p = p
        self.config = config
        self.reminder_tz = reminder_tz
        self.deadline = primary_deadline(p, now_utc)
        self.d = self.deadline.due_local.date()
        self.need = letters.required(p, config.letters.default_required)
        self.confirmed = letters.confirmed(p.id, professors)
        self.requested = letters.requested(p.id, professors)
        self.reason = _unverified(p, self.deadline)
        hh, mm = (int(x) for x in config.reminders.time_of_day.split(":"))
        self.time_of_day = time(hh, mm)

    def facts(self) -> list[str]:
        p, dl = self.p, self.deadline
        assumed = " (number assumed)" if self.need.assumed else ""
        return [
            f"{p.name} ({p.school})",
            f"{dl.label}: {_when(dl.due_local, dl.tz)}",
            f"Letters: {self.confirmed} confirmed, {self.requested} requested, "
            f"{self.need.count} required{assumed}",
            f"Data: Unverified · {self.reason}" if self.reason else "Data: Verified",
            f"Source: {dl.source_url}",
            f"{app_url(self.config)}/programmes/{p.id}",
        ]

    def rule(
        self, rule: str, day: date, title: str, subject: str | None = None, extra: list[str] | None = None
    ) -> Reminder:
        """A rule reminder: timed at time_of_day in the reminder zone, 15 minutes, one popup at the start."""
        tz = self.reminder_tz
        start = datetime.combine(day, self.time_of_day, tzinfo=ZoneInfo(tz))
        r = self.config.reminders
        return Reminder(
            id=f"{rule}:{self.p.id}" + (f":{subject}" if subject else ""),
            rule=rule,
            programme_id=self.p.id,
            subject_id=subject,
            category=CATEGORY.get(rule, "deadline"),
            date=day,
            start=start,
            tz=tz,
            minutes=r.rule_event_minutes,
            popups=(0,),
            title=f"[Hub] {self.p.short_name}: {title}",
            description="\n".join([*(extra or []), *self.facts()]),
        )


def _all(
    programmes: list[Programme],
    professors: list[Professor],
    reminder_tz: str,
    config: Config,
    now_utc: datetime,
) -> list[Reminder]:
    """Every reminder the rules give, whatever its date."""
    out: list[Reminder] = []
    r = config.reminders
    for p in programmes:
        b = _Builder(p, professors, reminder_tz, config, now_utc)
        submitted = p.status == ProgrammeStatus.SUBMITTED
        short = p.short_name
        if not submitted:
            if b.confirmed + b.requested < b.need.count:
                assumed = ", assumed" if b.need.assumed else ""
                out.append(
                    b.rule(
                        "letters_request_due",
                        b.d - timedelta(r.letters_request_days),
                        f"request letters ({b.need.count} needed{assumed})",
                    )
                )
            open_docs = [doc for doc in p.documents if doc.status in OPEN_DOCUMENTS]
            if open_docs:
                listed = ", ".join(doc.label for doc in open_docs)
                out.append(
                    b.rule(
                        "documents_due",
                        b.d - timedelta(r.documents_days),
                        f"send documents ({len(open_docs)} not sent)",
                        extra=[f"Not sent yet: {listed}"],
                    )
                )
            to_p = [x for prof in professors if (x := prof.letters.get(p.id))]
            live = [x for x in to_p if x.status != LetterStatus.DECLINED]
            submitted_letters = sum(x.status == LetterStatus.SUBMITTED for x in live)
            if any(x.status != LetterStatus.SUBMITTED for x in live) or len(live) < b.need.count:
                out.append(
                    b.rule(
                        "letters_check",
                        b.d - timedelta(r.letters_check_days),
                        f"check letters ({submitted_letters} of {b.need.count} submitted)",
                    )
                )
            if p.essays:
                n = len(p.essays)
                out.append(
                    b.rule(
                        "essay_final",
                        b.d - timedelta(r.essay_final_days),
                        f"finalise essays ({n} essay{'' if n == 1 else 's'})",
                    )
                )
            flag = " (unverified)" if b.reason else ""
            for days in r.deadline_alert_days:
                out.append(
                    b.rule(
                        f"deadline_alert_{days}",
                        b.d - timedelta(days),
                        f"deadline in {days} day{'' if days == 1 else 's'}{flag}",
                    )
                )
            dl = b.deadline
            out.append(
                Reminder(
                    id=f"deadline:{p.id}",
                    rule="deadline",
                    programme_id=p.id,
                    subject_id=None,
                    category="deadline",
                    date=b.d,
                    start=dl.due_local.replace(tzinfo=ZoneInfo(dl.tz)),
                    tz=dl.tz,
                    minutes=r.deadline_event_minutes,
                    popups=tuple(r.deadline_popups_minutes),
                    title=f"[Hub] {short}: {dl.label.lower()}{flag}",
                    description="\n".join(b.facts()),
                    timed_at_own_instant=True,
                )
            )
            for prof in professors:
                x = prof.letters.get(p.id)
                if x and x.status == LetterStatus.REQUESTED and x.requested_on:
                    out.append(
                        b.rule(
                            "letter_follow_up",
                            x.requested_on + timedelta(config.letters.follow_up_days),
                            f"follow up with {prof.name}",
                            subject=prof.id,
                            extra=[
                                f"Asked {prof.name} on {x.requested_on:%d %b %Y}; no answer recorded yet."
                            ],
                        )
                    )
        for iv in p.interviews:
            if iv.status != InterviewStatus.SCHEDULED:
                continue
            est = " (estimated)" if iv.estimated else ""
            where = [f"{iv.label}: {_when(iv.start_local, iv.tz)}{est}"]
            out.append(
                b.rule(
                    "interview_eve",
                    iv.start_local.date() - timedelta(r.interview_eve_days),
                    f"{iv.label} tomorrow{est}",
                    subject=iv.id,
                    extra=where,
                )
            )
            out.append(
                Reminder(
                    id=f"interview:{p.id}:{iv.id}",
                    rule="interview",
                    programme_id=p.id,
                    subject_id=iv.id,
                    category="interview",
                    date=iv.start_local.date(),
                    start=iv.start_local.replace(tzinfo=ZoneInfo(iv.tz)),
                    tz=iv.tz,
                    minutes=r.interview_event_minutes,
                    popups=tuple(r.interview_popups_minutes),
                    title=f"[Hub] {short}: {iv.label}{est}",
                    description="\n".join([*where, *b.facts()]),
                    timed_at_own_instant=True,
                )
            )
    return out


def _now(today: date, reminder_tz: str, now_utc: datetime | None) -> datetime:
    """The moment that picks the primary deadline: the given one, else the start of ``today``."""
    if now_utc is not None:
        return now_utc
    return to_instant(datetime.combine(today, time()), reminder_tz).utc


def reminders(
    programmes: list[Programme],
    professors: list[Professor],
    reminder_tz: str,
    config: Config,
    today: date,
    now_utc: datetime | None = None,
) -> list[Reminder]:
    """Every reminder dated ``today`` or later (§5.1), sorted by date, then rule order."""
    found = _all(programmes, professors, reminder_tz, config, _now(today, reminder_tz, now_utc))
    return sorted((x for x in found if x.date >= today), key=lambda x: x.sort_key)


def overdue(
    programmes: list[Programme],
    professors: list[Professor],
    reminder_tz: str,
    config: Config,
    today: date,
    now_utc: datetime | None = None,
) -> list[Reminder]:
    """Past tasks whose condition still holds, for programmes whose deadline date is today or later (Q2)."""
    now = _now(today, reminder_tz, now_utc)
    open_deadline = {p.id for p in programmes if primary_deadline(p, now).due_local.date() >= today}
    found = _all(programmes, professors, reminder_tz, config, now)
    return sorted(
        (
            replace(x, overdue=True)
            for x in found
            if x.rule in TASK_RULES and x.date < today and x.programme_id in open_deadline
        ),
        key=lambda x: x.sort_key,
    )
