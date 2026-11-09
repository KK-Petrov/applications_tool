"""Sync state in sync/: calendar_map.json, digest_state.json and last_sync.json (docs/01 §8)."""

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


class LastSync(DataModel):
    """The last ``hub sync`` (or Sync now), for the Settings page (DECISIONS #106 Q5)."""

    synced_at: AwareTimestamp
    dry_run: bool
    created: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0
    errors: int = 0
    error: str | None = None  # why the run stopped (reconnect Google, an unreadable data file), never data
