"""The daily digest (docs/05 §4; C-3-03, C-3-06): what is overdue, what is due soon, who hasn't answered, and
which deadlines are still unverified, emailed to the owner's own address.

``send`` takes **no recipient**: To and From are ``settings.owner_email``, read here, and an empty owner
address refuses. This is the only module that builds or sends email (tests/test_no_outbound_email.py). At most
one digest a day (``sync/digest_state.json``, the date in ``settings.reminder_tz``); a day with nothing to
report is skipped unless ``digest.send_when_empty``. Times are in the display zone with their abbreviation.
"""

from __future__ import annotations

import base64
import html as html_lib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any
from zoneinfo import ZoneInfo

from hub.config import Config
from hub.models.professor import LetterStatus
from hub.models.programme import ProgrammeStatus
from hub.rules import reminders as engine
from hub.rules.programmes import badge, dashboard_order, deadline_utc, primary_deadline
from hub.rules.timezones import MONTHS_SHORT, WEEKDAYS, format_date, format_instant, format_today, local_today
from hub.store import Store

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Section:
    heading: str
    lines: list[str]
    when_empty: str


@dataclass
class Digest:
    subject: str
    sections: list[Section]
    header: list[str]
    footer: str
    empty: bool
    text: str = ""
    html: str = ""


@dataclass
class SendResult:
    sent: bool
    reason: str | None = None  # why nothing was sent
    preview: str = ""  # the message as text (dry run)
    digest: Digest | None = field(default=None, repr=False)


def _when(r: engine.Reminder, display_tz: str, config: Config) -> str:
    if not r.timed_at_own_instant:
        return format_date(r.date, config.time)
    utc = r.start.astimezone(UTC)
    shown = format_instant(utc, display_tz, config.time, "long")
    if r.tz != display_tz:
        shown += f" ({format_instant(utc, r.tz, config.time, 'long')} school time)"
    return shown


def compose(store: Store, config: Config, now_utc: datetime) -> Digest:
    """The digest for ``now_utc`` (nothing sent)."""
    snapshot = store.load_all()
    settings = store.load_settings()
    display_tz, reminder_tz = settings.display_tz, settings.reminder_tz
    today = local_today(now_utc, display_tz)
    cfg = config.digest
    by_id = {p.id: p for p in snapshot.programmes}

    def line(r: engine.Reminder) -> str:
        text = f"- {_when(r, display_tz, config)} · {r.title.removeprefix('[Hub] ')}"
        if r.rule == "deadline":  # a deadline always carries its source (rule 10)
            text += f" · source: {primary_deadline(by_id[r.programme_id], now_utc).source_url}"
        return text

    late = engine.overdue(snapshot.programmes, snapshot.professors, reminder_tz, config, today, now_utc)
    horizon = today + timedelta(days=cfg.lookahead_days)
    soon = [
        r
        for r in engine.reminders(
            snapshot.programmes, snapshot.professors, reminder_tz, config, today, now_utc
        )
        if r.date < horizon
    ]
    waiting: list[tuple[Any, str, str]] = []
    for prof in snapshot.professors:
        for pid, letter in prof.letters.items():
            if letter.status != LetterStatus.REQUESTED or not letter.requested_on or pid not in by_id:
                continue
            days = (today - letter.requested_on).days
            if days > cfg.waiting_days:
                asked = format_date(letter.requested_on, config.time)
                text = f"- {prof.name} · {by_id[pid].short_name}, asked {asked} ({days} days ago)"
                waiting.append((letter.requested_on, prof.name, text))
    unverified: list[str] = []
    for p in dashboard_order(snapshot.programmes, now_utc):
        b = badge(p)
        dl = primary_deadline(p, now_utc)
        utc = deadline_utc(dl)
        if p.status == ProgrammeStatus.SUBMITTED or not b.unverified:
            continue
        if now_utc <= utc < now_utc + timedelta(days=cfg.unverified_window_days):
            shown = format_instant(utc, display_tz, config.time, "long")
            if dl.tz != display_tz:
                shown += f" ({format_instant(utc, dl.tz, config.time, 'long')} school time)"
            unverified.append(
                f"- {p.short_name} · {shown} · Unverified: {b.reason} · source: {dl.source_url}"
            )

    sections = [
        Section("Overdue", [line(r) for r in late], "- Nothing overdue."),
        Section(f"Next {cfg.lookahead_days} days", [line(r) for r in soon], "- Nothing due."),
        Section(
            f"Waiting on professors (asked more than {cfg.waiting_days} days ago)",
            [t for _, _, t in sorted(waiting)],
            "- Nobody.",
        ),
        Section(
            f"Unverified programmes with a deadline in the next {cfg.unverified_window_days} days",
            unverified,
            "- None.",
        ),
    ]
    n = len(soon)
    items = f"{n} item{'' if n == 1 else 's'} in the next {cfg.lookahead_days} days"
    local = now_utc.astimezone(ZoneInfo(display_tz))
    day = f"{WEEKDAYS[local.weekday()][:3]} {local.day} {MONTHS_SHORT[local.month - 1]}"
    subject = f"Application Hub · {day}: {items}"
    header = [
        f"Application Hub · {format_today(now_utc, display_tz)}",
        f"{items}. Times are in {display_tz}.",
    ]
    broken = [e.path for e in snapshot.errors]
    if broken:  # never silent: the digest may be missing what these files hold
        header.append(
            f"Some data files could not be read, so this digest may be incomplete: {', '.join(broken)}."
        )
    digest = Digest(
        subject=subject,
        sections=sections,
        header=header,
        footer=f"Open Application Hub: {engine.app_url(config)}/",
        empty=not (late or soon or waiting or unverified),
    )
    digest.text = _text(digest)
    digest.html = _html(digest)
    return digest


def _text(d: Digest) -> str:
    parts = ["\n".join(d.header)]
    for s in d.sections:
        parts.append("\n".join([s.heading, *(s.lines or [s.when_empty])]))
    parts.append(d.footer)
    return "\n\n".join(parts) + "\n"


def _html(d: Digest) -> str:
    e = html_lib.escape
    out = [
        "<!doctype html>",
        '<html lang="en"><body style="font-family: sans-serif">',
        f"<h1>{e(d.header[0])}</h1>",
    ]
    out += [f"<p>{e(h)}</p>" for h in d.header[1:]]
    for s in d.sections:
        out.append(f"<h2>{e(s.heading)}</h2>")
        out.append(
            "<ul>" + "".join(f"<li>{e(x.removeprefix('- '))}</li>" for x in s.lines) + "</ul>"
            if s.lines
            else f"<p>{e(s.when_empty.removeprefix('- '))}</p>"
        )
    url = d.footer.removeprefix("Open Application Hub: ")
    out.append(f'<p><a href="{e(url)}">Open Application Hub</a></p></body></html>')
    return "\n".join(out) + "\n"


def send(store: Store, service: Any, config: Config, now_utc: datetime, dry_run: bool = False) -> SendResult:
    """Send today's digest to the owner's own address, or say why not. There is no recipient argument."""
    settings = store.load_settings()
    owner = settings.owner_email
    if not owner:
        return SendResult(False, "No owner address in settings.yaml (owner_email), so nothing was sent.")
    digest = compose(store, config, now_utc)
    message = EmailMessage()
    message["To"] = owner
    message["From"] = owner
    message["Subject"] = digest.subject
    message.set_content(digest.text)
    message.add_alternative(digest.html, subtype="html")
    preview = f"To: {owner}\nFrom: {owner}\nSubject: {digest.subject}\n\n{digest.text}"
    if dry_run:
        return SendResult(False, "dry run", preview, digest)
    today = local_today(now_utc, settings.reminder_tz)
    state = store.load_digest_state()
    if state.last_sent_date == today:
        return SendResult(False, f"A digest was already sent today ({today.isoformat()}).", preview, digest)
    if digest.empty and not config.digest.send_when_empty:
        return SendResult(False, "nothing to report today, so no digest was sent.", preview, digest)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
    state.last_sent_date = today
    store.save_digest_state(state)
    log.info("digest sent to the owner address")
    return SendResult(True, None, preview, digest)
