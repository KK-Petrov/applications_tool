"""Sync state: ``sync/calendar_map.json`` and ``sync/digest_state.json`` (docs/01 §8). Used from Phase 3."""

from __future__ import annotations

from datetime import date

from pydantic import RootModel

from hub.models.common import AwareTimestamp, DataModel


class CalendarMapEntry(DataModel):
    event_id: str
    hash: str  # sha256 of the event body
    synced_at: AwareTimestamp


class CalendarMap(RootModel[dict[str, CalendarMapEntry]]):
    """Reminder ID → Google event. Rebuildable from the events' ``hub_reminder_id`` property."""


class DigestState(DataModel):
    last_sent_date: date | None = None  # in settings.reminder_tz
