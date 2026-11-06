"""Programme-level rules (docs/01 §3, docs/02 §3): primary deadline, progress, sort and the data badge."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hub.models.common import Verification
from hub.models.programme import Deadline, DocumentStatus, Programme
from hub.rules.timezones import to_instant

DEADLINE_CHANGED_REASON = (
    "Deadline changed, not checked"  # verified programme, edited deadline (DECISIONS #21)
)
NOT_CHECKED_REASON = "Not checked"  # unverified with no verification_note


@dataclass(frozen=True)
class Badge:
    """The "Unverified" data badge and its reason; ``unverified`` False means no badge."""

    unverified: bool
    reason: str | None


def deadline_utc(deadline: Deadline) -> datetime:
    """The deadline's instant (UTC)."""
    return to_instant(deadline.due_local, deadline.tz).utc


def primary_deadline(programme: Programme, now_utc: datetime) -> Deadline:
    """The earliest deadline whose instant has not passed; if all passed, the last one (DECISIONS #22)."""
    ordered = sorted(programme.deadlines, key=deadline_utc)
    return next((d for d in ordered if deadline_utc(d) >= now_utc), ordered[-1])


def requirements_progress(programme: Programme) -> tuple[int, int]:
    """(done, total) requirements."""
    return sum(r.done for r in programme.requirements), len(programme.requirements)


def documents_progress(programme: Programme) -> tuple[int, int]:
    """(received + waived, total) documents, shown as "0 / 4 received"."""
    done = {DocumentStatus.RECEIVED, DocumentStatus.WAIVED}
    return sum(d.status in done for d in programme.documents), len(programme.documents)


def dashboard_order(programmes: list[Programme], now_utc: datetime) -> list[Programme]:
    """Sorted by primary-deadline instant ascending; programmes whose primary deadline passed go last."""

    def key(p: Programme) -> tuple[bool, datetime, str]:
        instant = deadline_utc(primary_deadline(p, now_utc))
        return instant < now_utc, instant, p.short_name

    return sorted(programmes, key=key)


def badge(programme: Programme) -> Badge:
    """Unverified if the programme or any deadline is unverified, with the reason to show (docs/02 §3)."""
    if programme.verification == Verification.UNVERIFIED:
        return Badge(True, programme.verification_note or NOT_CHECKED_REASON)
    if any(d.verification == Verification.UNVERIFIED for d in programme.deadlines):
        return Badge(True, DEADLINE_CHANGED_REASON)
    return Badge(False, None)
