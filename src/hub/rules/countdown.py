"""Countdown to a deadline (docs/02 §2).

``days_left = floor((deadline_utc - now_utc) / 24 h)``, independent of the display zone. Within the
last 24 hours the label shows hours and minutes left (minutes floored); after the instant, "Passed".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class Urgency(StrEnum):
    NORMAL = "normal"
    ORANGE = "orange"  # <= urgency.orange_days
    RED = "red"  # <= urgency.red_days, and the last 24 hours
    PASSED = "passed"  # grey


@dataclass(frozen=True)
class Countdown:
    days: int  # floor of whole days left; negative once passed
    label: str  # "46 days", "1 day", "Today, 5 h left", "Passed"
    urgency: Urgency

    @property
    def passed(self) -> bool:
        return self.urgency is Urgency.PASSED


def countdown(deadline_utc: datetime, now_utc: datetime, orange_days: int, red_days: int) -> Countdown:
    """Days left and label for a deadline instant seen at ``now_utc`` (both aware).

    Labels (DECISIONS #25): "Today, 23 h 59 min left", "Today, 5 h left" (no minutes),
    "Today, 45 min left" (no hours); minutes are floored so the time left is never overstated.
    """
    remaining = deadline_utc - now_utc
    days = remaining // timedelta(days=1)
    if remaining < timedelta(0):
        return Countdown(days, "Passed", Urgency.PASSED)
    if days == 0:
        hours, rest = divmod(int(remaining.total_seconds()), 3600)
        minutes = rest // 60
        parts = [f"{hours} h"] if hours else []
        if minutes or not hours:
            parts.append(f"{minutes} min")
        return Countdown(0, f"Today, {' '.join(parts)} left", Urgency.RED)
    label = "1 day" if days == 1 else f"{days} days"
    if days <= red_days:
        urgency = Urgency.RED
    elif days <= orange_days:
        urgency = Urgency.ORANGE
    else:
        urgency = Urgency.NORMAL
    return Countdown(days, label, urgency)
