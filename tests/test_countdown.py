"""C-1-03: days left on the boundary cases of docs/02 §2 (Princeton deadline 2026-12-15 23:59 New York)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from hub.config import load_config
from hub.rules.countdown import Urgency, countdown
from hub.rules.timezones import to_instant

NY = ZoneInfo("America/New_York")
AMS = ZoneInfo("Europe/Amsterdam")
DEADLINE = datetime(2026, 12, 16, 4, 59, tzinfo=UTC)  # 2026-12-15 23:59 New York (EST, UTC-5)
URGENCY = load_config().urgency


def _left(now: datetime):
    return countdown(DEADLINE, now.astimezone(UTC), URGENCY.orange_days, URGENCY.red_days)


def test_deadline_is_the_princeton_instant() -> None:
    assert to_instant(datetime(2026, 12, 15, 23, 59), "America/New_York").utc == DEADLINE


# docs/02 §2, verbatim
@pytest.mark.parametrize(
    ("now", "days", "label"),
    [
        (datetime(2026, 10, 30, 12, 0, tzinfo=NY), 46, "46 days"),
        (datetime(2026, 12, 14, 23, 59, tzinfo=NY), 1, "1 day"),
        (datetime(2026, 12, 15, 0, 0, tzinfo=NY), 0, "Today, 23 h 59 min left"),
        (datetime(2026, 12, 16, 0, 0, tzinfo=NY), None, "Passed"),
        (datetime(2026, 12, 15, 23, 58, tzinfo=AMS), 0, "Today, 6 h 1 min left"),  # = 17:58 New York
    ],
    ids=["46-days", "1-day", "today-23h59", "passed", "today-from-amsterdam"],
)
def test_spec_boundary_cases(now: datetime, days: int | None, label: str) -> None:
    result = _left(now)
    assert result.label == label
    if days is None:
        assert result.passed and result.days < 0
    else:
        assert result.days == days and not result.passed


@pytest.mark.parametrize(
    ("before", "label"),
    [
        (timedelta(hours=5), "Today, 5 h left"),
        (timedelta(minutes=45), "Today, 45 min left"),
        (timedelta(minutes=59, seconds=59), "Today, 59 min left"),  # minutes floored
        (timedelta(hours=23, minutes=59, seconds=59), "Today, 23 h 59 min left"),
        (timedelta(0), "Today, 0 min left"),  # 0 <= remaining < 24 h
        (timedelta(days=1), "1 day"),
        (timedelta(days=2, seconds=-1), "1 day"),
        (timedelta(days=2), "2 days"),
        (timedelta(seconds=-1), "Passed"),
    ],
)
def test_labels_around_boundaries(before: timedelta, label: str) -> None:
    assert _left(DEADLINE - before).label == label


@pytest.mark.parametrize(
    ("days_before", "urgency"),
    [
        (15, Urgency.NORMAL),
        (14, Urgency.ORANGE),
        (3, Urgency.ORANGE),
        (2, Urgency.RED),
        (1, Urgency.RED),
    ],
)
def test_urgency_bands(days_before: int, urgency: Urgency) -> None:
    assert _left(DEADLINE - timedelta(days=days_before)).urgency is urgency


def test_today_is_red_and_passed_is_grey() -> None:
    assert _left(DEADLINE - timedelta(hours=3)).urgency is Urgency.RED
    assert _left(DEADLINE + timedelta(minutes=1)).urgency is Urgency.PASSED


def test_independent_of_the_zone_now_is_expressed_in() -> None:
    now = datetime(2026, 11, 20, 20, 30, tzinfo=UTC)
    results = {_left(now.astimezone(ZoneInfo(z))) for z in ("America/New_York", "Europe/Amsterdam", "UTC")}
    assert len(results) == 1
