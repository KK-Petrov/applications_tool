"""Sync state: ``sync/calendar_map.json`` and ``sync/digest_state.json`` (docs/01 §8). Used from Phase 3."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import RootModel

from hub.models.common import DataModel


class CalendarMapEntry(DataModel):
    event_id: str
    hash: str  # sha256 of the event body
    synced_at: datetime


class CalendarMap(RootModel[dict[str, CalendarMapEntry]]):
    """Reminder ID → Google event. Rebuildable from the events' ``hub_reminder_id`` property."""


class DigestState(DataModel):
    last_sent_date: date | None = None  # in settings.reminder_tz
