"""Time-zone rules (docs/02 §1): deadline instants, conversion and zone-aware formatting.

A deadline is a wall-clock time in the school's IANA zone. Its *instant* is that wall-clock time
interpreted in the zone: ambiguous times (DST fall-back) take ``fold=0``, the earlier instant
(conservative); non-existent times (spring-forward gap) move forward by the gap and are flagged
``adjusted`` so the UI can say so. Every printed time carries its zone abbreviation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from hub.config import TimeConfig

MONTHS_SHORT = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MONTHS_LONG = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
               "October", "November", "December")  # fmt: skip
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


@dataclass(frozen=True)
class Instant:
    """A point in time (aware, UTC) plus whether the local time fell in a DST gap and was moved."""

    utc: datetime
    adjusted: bool = False


def to_instant(local: datetime, tz: str) -> Instant:
    """Interpret the naive wall-clock ``local`` in ``tz`` (fold 0; gap shifted forward and flagged).

    zoneinfo resolves a non-existent time (fold 0) with the offset in force *before* the transition,
    which is exactly "moved forward by the gap" (02:30 EST = 03:30 EDT). It is detected by the round
    trip: converting back does not give the same wall-clock time.
    """
    if local.tzinfo is not None:
        raise ValueError("to_instant expects a naive local time paired with a zone")
    zone = ZoneInfo(tz)
    utc = local.replace(tzinfo=zone, fold=0).astimezone(UTC)
    back = utc.astimezone(zone).replace(tzinfo=None)
    return Instant(utc=utc, adjusted=back != local)


def abbreviation(tz: str, instant_utc: datetime, time_cfg: TimeConfig) -> str:
    """Zone abbreviation printed next to a time: config map (ET, CT…), else tzname (CET, CEST, UTC)."""
    if tz in time_cfg.zone_abbreviations:
        return time_cfg.zone_abbreviations[tz]
    return instant_utc.astimezone(ZoneInfo(tz)).tzname() or tz


def _clock(tz: str, time_cfg: TimeConfig) -> str:
    for zone in time_cfg.display_zones:
        if zone.tz == tz:
            return zone.clock
    return time_cfg.default_clock


def _place(tz: str, time_cfg: TimeConfig) -> str | None:
    return next((z.place for z in time_cfg.display_zones if z.tz == tz), None)


def format_clock(local: datetime, tz: str, time_cfg: TimeConfig) -> str:
    """The time of day only, on the zone's clock: ``11:59 pm`` (US) or ``05:59`` (Rotterdam)."""
    formats = time_cfg.formats
    minute = f"{local.minute:02d}"
    if _clock(tz, time_cfg) == "12h":
        ampm = "am" if local.hour < 12 else "pm"
        return formats.time_12h.format(hour12=local.hour % 12 or 12, minute=minute, ampm=ampm)
    return formats.time_24h.format(hour24=f"{local.hour:02d}", minute=minute)


def format_instant(
    instant_utc: datetime, tz: str, time_cfg: TimeConfig, style: str = "long", with_place: bool = False
) -> str:
    """``15 Dec 2026, 11:59 pm ET`` (style long) or ``16 Dec, 05:59 CET (Rotterdam)`` (short, with place).

    ``with_place`` adds the display zone's place name (config ``display_zones[].place``); it is used for
    times converted to the display zone, never for the school-zone original (DECISIONS #20).
    """
    local = instant_utc.astimezone(ZoneInfo(tz))
    zone = abbreviation(tz, instant_utc, time_cfg)
    place = _place(tz, time_cfg) if with_place else None
    if place:
        zone = f"{zone} ({place})"
    pattern = time_cfg.formats.short if style == "short" else time_cfg.formats.long
    return pattern.format(
        day=local.day,
        month=MONTHS_SHORT[local.month - 1],
        year=local.year,
        time=format_clock(local, tz, time_cfg),
        zone=zone,
    )


def format_date(day: date, time_cfg: TimeConfig) -> str:
    """A date without a time, e.g. ``3 Nov 2026`` (request-by dates, checked dates)."""
    return time_cfg.formats.date.format(day=day.day, month=MONTHS_SHORT[day.month - 1], year=day.year)


def format_today(now_utc: datetime, tz: str) -> str:
    """Header date in the display zone, e.g. ``Friday 30 October 2026`` (no zero padding on any OS)."""
    local = now_utc.astimezone(ZoneInfo(tz))
    return f"{WEEKDAYS[local.weekday()]} {local.day} {MONTHS_LONG[local.month - 1]} {local.year}"


def local_today(now_utc: datetime, tz: str) -> date:
    """Today's calendar date in ``tz``."""
    return now_utc.astimezone(ZoneInfo(tz)).date()


def format_deadline_text(
    due_local: datetime, tz: str, time_known: bool, time_cfg: TimeConfig, pattern: str, date_only: str
) -> str:
    """A deadline as the request email states it, in the **school's** zone: ``15 December 2026, 11:59 pm ET``.

    The model copies this text exactly (docs/04 §2.1). A deadline whose time is not confirmed is given as its
    date only (``7 January 2027``), never with the assumed time (DECISIONS #48). A local time inside a DST gap
    is printed as the instant it becomes (docs/02 §1).
    """
    instant = to_instant(due_local, tz)
    local = instant.utc.astimezone(ZoneInfo(tz))
    fields = {"day": local.day, "month_long": MONTHS_LONG[local.month - 1], "year": local.year}
    if not time_known:
        return date_only.format(**fields)
    return pattern.format(
        **fields, time=format_clock(local, tz, time_cfg), zone=abbreviation(tz, instant.utc, time_cfg)
    )
