"""C-1-02: time-zone conversion on the 20 reference cases of docs/02 §1, plus zone-aware formatting.

The table below is copied verbatim from docs/02 §1 (expected values derived by hand there).
A failure here means a deadline would be shown an hour or a day off.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from hub.config import load_config
from hub.rules.timezones import (
    abbreviation,
    format_date,
    format_instant,
    format_today,
    local_today,
    to_instant,
)

# (case, local time, from zone, to zone, expected local time in "to", adjusted)
CASES = [
    (1, "2026-12-15 23:59", "America/New_York", "Europe/Amsterdam", "2026-12-16 05:59", False),
    (2, "2026-12-15 23:59", "America/New_York", "UTC", "2026-12-16 04:59", False),
    (3, "2027-01-07 23:59", "America/Chicago", "America/New_York", "2027-01-08 00:59", False),
    (4, "2027-01-07 23:59", "America/Chicago", "Europe/Amsterdam", "2027-01-08 06:59", False),
    (5, "2027-01-05 17:00", "America/New_York", "Europe/Amsterdam", "2027-01-05 23:00", False),
    (6, "2026-10-28 12:00", "America/New_York", "Europe/Amsterdam", "2026-10-28 17:00", False),
    (7, "2026-10-24 12:00", "America/New_York", "Europe/Amsterdam", "2026-10-24 18:00", False),
    (8, "2026-11-02 12:00", "America/New_York", "Europe/Amsterdam", "2026-11-02 18:00", False),
    (9, "2026-11-01 01:30", "America/New_York", "UTC", "2026-11-01 05:30", False),  # ambiguous, fold 0
    (10, "2027-03-14 02:30", "America/New_York", "UTC", "2027-03-14 07:30", True),  # gap, adjusted
    (11, "2027-03-20 12:00", "America/New_York", "Europe/Amsterdam", "2027-03-20 17:00", False),
    (12, "2027-03-29 12:00", "America/New_York", "Europe/Amsterdam", "2027-03-29 18:00", False),
    (13, "2026-12-01 09:00", "Europe/Amsterdam", "America/New_York", "2026-12-01 03:00", False),
    (14, "2027-04-15 23:59", "America/New_York", "Europe/Amsterdam", "2027-04-16 05:59", False),
    (15, "2027-04-15 23:59", "America/New_York", "UTC", "2027-04-16 03:59", False),
    (16, "2026-12-31 23:59", "America/Los_Angeles", "America/New_York", "2027-01-01 02:59", False),
    (17, "2026-12-31 23:59", "America/Los_Angeles", "Europe/Amsterdam", "2027-01-01 08:59", False),
    (18, "2027-01-15 23:59", "America/New_York", "America/Chicago", "2027-01-15 22:59", False),
    (19, "2026-10-25 02:30", "Europe/Amsterdam", "UTC", "2026-10-25 00:30", False),  # ambiguous, fold 0
    (20, "2026-11-01 12:00", "UTC", "America/New_York", "2026-11-01 07:00", False),
]
FMT = "%Y-%m-%d %H:%M"


def test_there_are_exactly_20_reference_cases() -> None:
    assert [c[0] for c in CASES] == list(range(1, 21))


@pytest.mark.parametrize(
    ("case", "local", "from_tz", "to_tz", "expected", "adjusted"), CASES, ids=[f"case-{c[0]}" for c in CASES]
)
def test_reference_case(
    case: int, local: str, from_tz: str, to_tz: str, expected: str, adjusted: bool
) -> None:
    instant = to_instant(datetime.strptime(local, FMT), from_tz)
    assert instant.utc.tzinfo is not None
    assert instant.utc.astimezone(ZoneInfo(to_tz)).strftime(FMT) == expected
    assert instant.adjusted is adjusted


def test_instant_is_utc() -> None:
    instant = to_instant(datetime(2026, 12, 15, 23, 59), "America/New_York")
    assert instant.utc == datetime(2026, 12, 16, 4, 59, tzinfo=UTC)
    assert instant.utc.utcoffset().total_seconds() == 0


def test_ambiguous_time_takes_the_earlier_instant() -> None:
    first = to_instant(datetime(2026, 11, 1, 1, 30), "America/New_York")
    assert first.utc == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)  # EDT, not EST (06:30)
    assert not first.adjusted


def test_gap_time_shows_shifted_local_time() -> None:
    instant = to_instant(datetime(2027, 3, 14, 2, 30), "America/New_York")
    assert instant.utc.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M") == "03:30"


@pytest.fixture(scope="module")
def time_cfg():
    return load_config().time


@pytest.mark.parametrize(
    ("tz", "when", "expected"),
    [
        ("America/New_York", datetime(2026, 12, 16, 4, 59, tzinfo=UTC), "ET"),
        ("America/New_York", datetime(2026, 7, 1, 12, 0, tzinfo=UTC), "ET"),
        ("America/Chicago", datetime(2027, 1, 8, 5, 59, tzinfo=UTC), "CT"),
        ("Europe/Amsterdam", datetime(2026, 12, 16, 4, 59, tzinfo=UTC), "CET"),
        ("Europe/Amsterdam", datetime(2026, 10, 24, 16, 0, tzinfo=UTC), "CEST"),
        ("UTC", datetime(2026, 12, 16, 4, 59, tzinfo=UTC), "UTC"),
    ],
)
def test_abbreviation(time_cfg, tz: str, when: datetime, expected: str) -> None:
    assert abbreviation(tz, when, time_cfg) == expected


PRINCETON = datetime(2026, 12, 16, 4, 59, tzinfo=UTC)  # 15 Dec 2026, 23:59 New York


def test_format_school_time_long(time_cfg) -> None:
    assert format_instant(PRINCETON, "America/New_York", time_cfg) == "15 Dec 2026, 11:59 pm ET"


def test_format_rotterdam_both_abbreviation_and_place(time_cfg) -> None:
    got = format_instant(PRINCETON, "Europe/Amsterdam", time_cfg, with_place=True)
    assert got == "16 Dec 2026, 05:59 CET (Rotterdam)"
    short = format_instant(PRINCETON, "Europe/Amsterdam", time_cfg, style="short", with_place=True)
    assert short == "16 Dec, 05:59 CET (Rotterdam)"


def test_format_summer_time_in_rotterdam(time_cfg) -> None:
    got = format_instant(
        datetime(2026, 10, 24, 16, 0, tzinfo=UTC), "Europe/Amsterdam", time_cfg, with_place=True
    )
    assert got == "24 Oct 2026, 18:00 CEST (Rotterdam)"


def test_eastern_display_zone_has_no_place(time_cfg) -> None:
    assert (
        format_instant(PRINCETON, "America/New_York", time_cfg, with_place=True) == "15 Dec 2026, 11:59 pm ET"
    )


def test_school_zone_original_never_has_place(time_cfg) -> None:
    assert format_instant(PRINCETON, "Europe/Amsterdam", time_cfg) == "16 Dec 2026, 05:59 CET"


@pytest.mark.parametrize(
    ("utc", "tz", "expected"),
    [
        (datetime(2027, 1, 8, 5, 59, tzinfo=UTC), "America/Chicago", "7 Jan 2027, 11:59 pm CT"),
        (datetime(2027, 1, 8, 5, 59, tzinfo=UTC), "America/New_York", "8 Jan 2027, 12:59 am ET"),
        (datetime(2026, 11, 1, 17, 0, tzinfo=UTC), "America/New_York", "1 Nov 2026, 12:00 pm ET"),
        (datetime(2027, 1, 5, 22, 0, tzinfo=UTC), "America/New_York", "5 Jan 2027, 5:00 pm ET"),
        (datetime(2027, 1, 5, 22, 0, tzinfo=UTC), "Europe/Amsterdam", "5 Jan 2027, 23:00 CET"),
        (datetime(2026, 12, 1, 8, 0, tzinfo=UTC), "Europe/Amsterdam", "1 Dec 2026, 09:00 CET"),
    ],
)
def test_clock_and_padding(time_cfg, utc: datetime, tz: str, expected: str) -> None:
    assert format_instant(utc, tz, time_cfg) == expected


def test_format_date_and_today(time_cfg) -> None:
    assert format_date(date(2026, 11, 3), time_cfg) == "3 Nov 2026"
    now = datetime(2026, 10, 30, 16, 0, tzinfo=UTC)
    assert format_today(now, "America/New_York") == "Friday 30 October 2026"
    late = datetime(2026, 10, 31, 2, 0, tzinfo=UTC)  # 22:00 New York, 03:00 Amsterdam
    assert local_today(late, "America/New_York") == date(2026, 10, 30)
    assert local_today(late, "Europe/Amsterdam") == date(2026, 10, 31)
