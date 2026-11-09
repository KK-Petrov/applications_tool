"""Calendar sync (docs/05 §2; C-3-02): the reminder engine's output, mirrored into the Applications calendar.

Every run lists the calendar's events once (with deleted ones) and reconciles them with the map
``sync/calendar_map.json`` (reminder id → event id + hash of the body sent): an event that is gone, deleted
by hand (``cancelled``) or not in the map is found by its ``hub_reminder_id`` property, so the map is rebuilt
when missing and duplicates sharing an id are deleted. Then each desired reminder is inserted (not in the
map), patched (hash changed) or left alone; mapped reminders no longer desired are deleted. The map is saved
after each successful call, so a crash part way never causes duplicates. Only the stored calendar is touched;
events without ``hub_reminder_id`` are never touched. 429 and 5xx are retried with ``google.backoff_s``; a
failing call is counted and the run goes on. Logs carry counts and ids, never titles or descriptions.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from hub.config import Config
from hub.models.sync import CalendarMapEntry, LastSync
from hub.rules import reminders as engine
from hub.rules.timezones import local_today
from hub.store import DataError, Store

log = logging.getLogger(__name__)
HUB_ID = "hub_reminder_id"
HUB_HASH = "hub_hash"  # the body's hash, so a rebuilt map knows what each event holds
RETRY = frozenset({429, 500, 502, 503, 504})
GONE = frozenset({404, 410})
RECONNECT = "Google refused the saved sign-in. Reconnect Google: run `uv run hub connect-google`."


class SyncStopped(Exception):
    """The run cannot go on (Google sign-in refused); nothing more is called."""


@dataclass
class SyncResult:
    counts: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(("created", "updated", "deleted", "unchanged", "errors"), 0)
    )
    calendar_id: str | None = None
    dry_run: bool = False
    error: str | None = None  # why the run stopped before syncing (never data)


def event_body(r: engine.Reminder) -> dict[str, Any]:
    """The Google event for a reminder (docs/05 §2.2), without the hash property."""
    start = r.start.replace(tzinfo=None)
    end = start + timedelta(minutes=r.minutes)
    return {
        "summary": r.title,
        "description": r.description,
        "start": {"dateTime": start.isoformat(timespec="seconds"), "timeZone": r.tz},
        "end": {"dateTime": end.isoformat(timespec="seconds"), "timeZone": r.tz},
        "reminders": {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": m} for m in r.popups],
        },
        "extendedProperties": {"private": {HUB_ID: r.id}},
    }


def body_hash(body: dict[str, Any]) -> str:
    """sha256 of the canonical JSON of the body (docs/05 §2.2)."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _status(err: HttpError) -> int:
    return int(getattr(err, "status_code", None) or err.resp.status)


class _Google:
    """Calls with retry and the run's bookkeeping."""

    def __init__(self, service: Any, config: Config, sleep: Callable[[float], None]) -> None:
        self.service = service
        self.tries = max(1, config.google.max_retries)
        self.backoff = config.google.backoff_s or [1.0]
        self.sleep = sleep

    def call(self, request: Any) -> Any:
        for attempt in range(self.tries):
            try:
                return request.execute()
            except RefreshError as err:
                raise SyncStopped(RECONNECT) from err
            except HttpError as err:
                status = _status(err)
                if status in (401, 403):
                    raise SyncStopped(RECONNECT) from err
                if status in RETRY and attempt < self.tries - 1:
                    self.sleep(self.backoff[min(attempt, len(self.backoff) - 1)])
                    continue
                raise
        raise AssertionError("unreachable")

    def list_events(self, calendar_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            page = self.call(
                self.service.events().list(
                    calendarId=calendar_id, pageToken=token, showDeleted=True, maxResults=250
                )
            )
            items += page.get("items", [])
            token = page.get("nextPageToken")
            if not token:
                return items


def _hub_id(event: dict[str, Any]) -> str | None:
    return ((event.get("extendedProperties") or {}).get("private") or {}).get(HUB_ID)


def _unreadable(store: Store) -> str | None:
    """A programme or professor file that can't be read would look resolved, and its events would be deleted:
    the sync refuses (fail closed)."""
    snapshot = store.load_all()
    broken = [
        e.path for e in snapshot.errors if e.path.startswith(("programmes/", "professors/", "settings"))
    ]
    if snapshot.settings is None and not broken:
        broken = ["settings.yaml"]
    if not broken:
        return None
    return (
        f"{', '.join(broken)} can't be read, so nothing was synced (its events would look resolved). "
        "Fix it first."
    )


def sync(
    store: Store,
    service: Any,
    config: Config,
    now_utc: datetime,
    dry_run: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> SyncResult:
    """One sync run; returns the counts. ``dry_run`` works them out from the map alone and calls nothing."""
    result = SyncResult(dry_run=dry_run)
    stop = _unreadable(store)
    if stop:
        result.error = stop
        _record(store, result, now_utc)
        log.warning("sync stopped: unreadable data file")
        return result
    snapshot = store.load_all()
    settings = snapshot.settings
    assert settings is not None
    tz = settings.reminder_tz
    today = local_today(now_utc, tz)
    desired = {
        r.id: r
        for r in engine.reminders(snapshot.programmes, snapshot.professors, tz, config, today, now_utc)
    }
    bodies = {rid: event_body(r) for rid, r in desired.items()}
    hashes = {rid: body_hash(b) for rid, b in bodies.items()}
    try:
        mapping = store.load_calendar_map()
    except DataError:
        log.warning("sync: %s unreadable, rebuilt from the calendar", "sync/calendar_map.json")
        mapping = None
    counts = result.counts

    if dry_run:
        known = (mapping or {}) if settings.calendar_id else {}
        for rid, h in hashes.items():
            entry = known.get(rid)
            counts["created" if entry is None else "unchanged" if entry.hash == h else "updated"] += 1
        counts["deleted"] = len(set(known) - set(hashes))
        result.calendar_id = settings.calendar_id
        _record(store, result, now_utc)
        log.info("sync dry run: %s", _summary(counts))
        return result

    google = _Google(service, config, sleep)
    try:
        cid, mapping = _calendar(store, google, config, settings, mapping)
        result.calendar_id = cid
        mapping = _reconcile(store, google, cid, mapping or {}, counts, now_utc)
        for rid in sorted(desired, key=lambda x: desired[x].sort_key):
            _push(store, google, cid, rid, bodies[rid], hashes[rid], mapping, counts, now_utc)
        for rid in sorted(set(mapping) - set(desired)):
            _remove(store, google, cid, rid, mapping, counts)
    except SyncStopped as err:
        result.error = str(err)
        log.warning("sync stopped: Google sign-in refused")
    _record(store, result, now_utc)
    log.info("sync: %s", _summary(counts))
    return result


def _summary(counts: dict[str, int]) -> str:
    return ", ".join(f"{k} {v}" for k, v in counts.items())


def _record(store: Store, result: SyncResult, now_utc: datetime) -> None:
    store.save_last_sync(
        LastSync(synced_at=now_utc, dry_run=result.dry_run, error=result.error, **result.counts)
    )


def _calendar(
    store: Store, google: _Google, config: Config, settings: Any, mapping: dict[str, CalendarMapEntry] | None
) -> tuple[str, dict[str, CalendarMapEntry] | None]:
    """The stored calendar, created when there is none; recreated (once, logged) only when ``calendars.get``
    on the stored id returns 404 (DECISIONS #106 Q8)."""
    cid = settings.calendar_id
    if cid:
        try:
            google.call(google.service.calendars().get(calendarId=cid))
            return cid, mapping
        except HttpError as err:
            if _status(err) != 404:
                raise SyncStopped(
                    f"Google Calendar did not answer for the Applications calendar ({_status(err)})."
                ) from err
            log.info("sync: calendar %s is gone; recreated", cid)
            mapping = {}
            store.save_calendar_map({})
    created = google.call(
        google.service.calendars().insert(
            body={"summary": config.google.calendar_name, "timeZone": settings.reminder_tz}
        )
    )
    settings.calendar_id = created["id"]
    store.save_settings(settings)
    log.info("sync: created calendar %s", created["id"])
    return created["id"], mapping


def _reconcile(
    store: Store,
    google: _Google,
    cid: str,
    mapping: dict[str, CalendarMapEntry],
    counts: dict[str, int],
    now_utc: datetime,
) -> dict[str, CalendarMapEntry]:
    """The map as the calendar has it: live events by hub id (duplicates deleted), the mapped one kept."""
    live: dict[str, list[dict[str, Any]]] = {}
    for event in google.list_events(cid):
        hub = _hub_id(event)
        if hub and event.get("status") != "cancelled":
            live.setdefault(hub, []).append(event)
    rebuilt: dict[str, CalendarMapEntry] = {}
    for hub, events in sorted(live.items()):
        mapped = mapping.get(hub)
        keep = next((e for e in events if mapped and e["id"] == mapped.event_id), events[0])
        for extra in events:
            if extra is not keep:
                try:
                    google.call(google.service.events().delete(calendarId=cid, eventId=extra["id"]))
                    counts["deleted"] += 1
                    log.info("sync: deleted duplicate event %s of %s", extra["id"], hub)
                except HttpError as err:
                    if _status(err) not in GONE:
                        counts["errors"] += 1
        if mapped and mapped.event_id == keep["id"]:
            rebuilt[hub] = mapped
        else:
            stored = ((keep.get("extendedProperties") or {}).get("private") or {}).get(HUB_HASH, "")
            rebuilt[hub] = CalendarMapEntry(event_id=keep["id"], hash=stored, synced_at=now_utc)
    # a mapped event that is gone, or cancelled on the phone, is dropped: it is inserted again below
    if rebuilt != mapping:
        store.save_calendar_map(rebuilt)
    return rebuilt


def _push(
    store: Store,
    google: _Google,
    cid: str,
    rid: str,
    body: dict[str, Any],
    digest: str,
    mapping: dict[str, CalendarMapEntry],
    counts: dict[str, int],
    now_utc: datetime,
) -> None:
    sent = {**body, "extendedProperties": {"private": {HUB_ID: rid, HUB_HASH: digest}}}
    entry = mapping.get(rid)
    try:
        if entry is not None and entry.hash == digest:
            counts["unchanged"] += 1
            return
        if entry is not None:
            try:
                google.call(google.service.events().patch(calendarId=cid, eventId=entry.event_id, body=sent))
                mapping[rid] = CalendarMapEntry(event_id=entry.event_id, hash=digest, synced_at=now_utc)
                store.save_calendar_map(mapping)
                counts["updated"] += 1
                return
            except HttpError as err:
                if _status(err) not in GONE:
                    raise
                log.info("sync: event %s of %s is gone; inserted again", entry.event_id, rid)
                try:  # make sure the old one is gone too, so a wrong "gone" can never leave a duplicate
                    google.call(google.service.events().delete(calendarId=cid, eventId=entry.event_id))
                except HttpError as gone:
                    if _status(gone) not in GONE:
                        raise
                del mapping[rid]
                store.save_calendar_map(mapping)
        created = google.call(google.service.events().insert(calendarId=cid, body=sent))
        mapping[rid] = CalendarMapEntry(event_id=created["id"], hash=digest, synced_at=now_utc)
        store.save_calendar_map(mapping)
        counts["created"] += 1
    except HttpError as err:
        counts["errors"] += 1
        log.warning("sync: %s failed (%s)", rid, _status(err))


def _remove(
    store: Store,
    google: _Google,
    cid: str,
    rid: str,
    mapping: dict[str, CalendarMapEntry],
    counts: dict[str, int],
) -> None:
    try:
        google.call(google.service.events().delete(calendarId=cid, eventId=mapping[rid].event_id))
    except HttpError as err:
        if _status(err) not in GONE:
            counts["errors"] += 1
            log.warning("sync: deleting %s failed (%s)", rid, _status(err))
            return
    del mapping[rid]
    store.save_calendar_map(mapping)
    counts["deleted"] += 1
