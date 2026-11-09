"""Calendar sync against the fake Google API (docs/05 §2; C-3-02).

The reminders fixture (tests/fixtures/reminders/, 30 reminders on 30 Oct 2026) synced into the "Applications"
calendar: idempotent (two runs, no duplicates), a changed deadline patches its events, resolved reminders are
deleted, a crash part way leaves no duplicates, the map is rebuilt from the events' ``hub_reminder_id``, a
mapped event that is gone (404), deleted (410) or cancelled on the phone is inserted again, 429/5xx are
retried, dry-run calls nothing, nothing but the stored calendar is touched; plus the Phase 3 approval
additions (DECISIONS #106): the calendar is recreated only when ``calendars.get`` 404s, a reused professor
id, a new reminder zone.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from fakes.fake_google import FakeGoogle
from hub.config import load_config
from hub.google import calendar_sync
from hub.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "reminders"
NOW = datetime(2026, 10, 30, 16, 0, tzinfo=UTC)  # 30 Oct 2026, 12:00 New York
HUB_ID = "hub_reminder_id"


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "data"
    shutil.copytree(FIXTURE, target)
    monkeypatch.setenv("HUB_DATA_DIR", str(target))
    return target


@pytest.fixture
def fake() -> FakeGoogle:
    return FakeGoogle()


def _sync(fake: FakeGoogle, dry_run: bool = False, now: datetime = NOW, sleeps: list | None = None):  # noqa: ANN202
    config = load_config()
    return calendar_sync.sync(
        Store.from_config(config),
        fake.calendar(),
        config,
        now,
        dry_run=dry_run,
        sleep=(sleeps.append if sleeps is not None else (lambda _s: None)),
    )


def _calendar_id() -> str:
    return Store.from_config(load_config()).load_settings().calendar_id or ""


def _ids(fake: FakeGoogle) -> list[str]:
    return [e["extendedProperties"]["private"][HUB_ID] for e in fake.live_events(_calendar_id())]


def _map(data: Path) -> dict:
    return json.loads((data / "sync" / "calendar_map.json").read_text(encoding="utf-8"))


def _edit(path: Path, change) -> None:  # noqa: ANN001
    yaml = YAML()
    doc = yaml.load(path.read_text(encoding="utf-8"))
    change(doc)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        yaml.dump(doc, fh)


def _event(fake: FakeGoogle, reminder_id: str) -> dict:
    return next(
        e
        for e in fake.live_events(_calendar_id())
        if e["extendedProperties"]["private"][HUB_ID] == reminder_id
    )


# ---- the first run, and idempotence ------------------------------------------------------------------


def test_the_first_run_creates_the_calendar_and_every_event(data: Path, fake: FakeGoogle) -> None:
    result = _sync(fake)
    assert result.counts == {"created": 30, "updated": 0, "deleted": 0, "unchanged": 0, "errors": 0}
    cid = _calendar_id()
    assert fake.calendars[cid] == {"summary": "Applications", "timeZone": "America/New_York"}
    assert sorted(_ids(fake)) == sorted(_map(data))
    assert len(_ids(fake)) == 30
    event = _event(fake, "deadline:princeton-mfin")
    assert event["start"] == {"dateTime": "2026-12-15T23:59:00", "timeZone": "America/New_York"}
    assert event["end"] == {"dateTime": "2026-12-16T00:29:00", "timeZone": "America/New_York"}
    assert event["reminders"] == {
        "useDefault": False,
        "overrides": [{"method": "popup", "minutes": 1440}, {"method": "popup", "minutes": 120}],
    }


def test_two_runs_create_no_duplicates(data: Path, fake: FakeGoogle) -> None:
    _sync(fake)
    inserts = fake.count("events.insert")
    result = _sync(fake)
    assert result.counts == {"created": 0, "updated": 0, "deleted": 0, "unchanged": 30, "errors": 0}
    assert fake.count("events.insert") == inserts and fake.count("calendars.insert") == 1
    assert len(_ids(fake)) == len(set(_ids(fake))) == 30


def test_a_changed_deadline_updates_its_events(data: Path, fake: FakeGoogle) -> None:
    _sync(fake)
    old_id = _event(fake, "deadline:princeton-mfin")["id"]
    _edit(
        data / "programmes" / "princeton-mfin.yaml",
        lambda d: d["deadlines"][0].update(due_local="2026-12-20T23:59"),
    )
    result = _sync(fake)
    assert result.counts["created"] == 0 and result.counts["deleted"] == 0
    assert (
        result.counts["updated"] == 12
    )  # Princeton's 11 rows and its follow-up (the facts name the deadline)
    moved = _event(fake, "deadline:princeton-mfin")
    assert moved["id"] == old_id and moved["start"]["dateTime"] == "2026-12-20T23:59:00"
    assert len(_ids(fake)) == 30


def test_resolved_reminders_are_removed(data: Path, fake: FakeGoogle) -> None:
    _sync(fake)
    _edit(data / "programmes" / "princeton-mfin.yaml", lambda d: d.update(status="submitted"))
    result = _sync(fake)
    assert result.counts["deleted"] == 10  # rows 1–9 and the follow-up; the interview rows stay
    assert len(_ids(fake)) == 20 and len(_map(data)) == 20
    assert "interview:princeton-mfin:math-test" in _ids(fake)


# ---- robustness ------------------------------------------------------------------------------------


def test_a_crash_part_way_leaves_no_duplicates(
    data: Path, fake: FakeGoogle, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fakes import fake_google

    real = fake_google._Events.insert
    calls = {"n": 0}

    def insert(self, calendarId, body):  # noqa: ANN001, ANN202, N803
        calls["n"] += 1
        if calls["n"] == 6:
            raise RuntimeError("the laptop lost power")
        return real(self, calendarId, body)

    monkeypatch.setattr(fake_google._Events, "insert", insert)
    with pytest.raises(RuntimeError):
        _sync(fake)
    assert len(_map(data)) == 5  # saved after each successful call
    monkeypatch.setattr(fake_google._Events, "insert", real)
    result = _sync(fake)
    assert result.counts["created"] == 25 and result.counts["unchanged"] == 5
    assert len(_ids(fake)) == len(set(_ids(fake))) == 30


def test_a_missing_map_is_rebuilt_from_the_events(data: Path, fake: FakeGoogle) -> None:
    fake.page_size = 7  # the listing takes several pages
    _sync(fake)
    (data / "sync" / "calendar_map.json").unlink()
    result = _sync(fake)
    assert result.counts == {"created": 0, "updated": 0, "deleted": 0, "unchanged": 30, "errors": 0}
    assert len(_map(data)) == 30 and len(_ids(fake)) == 30


def test_duplicate_events_sharing_an_id_are_deleted(data: Path, fake: FakeGoogle) -> None:
    _sync(fake)
    cid = _calendar_id()
    copy_of = {k: v for k, v in _event(fake, "deadline:booth-mif").items() if k not in ("id", "status")}
    fake.calendar().events().insert(calendarId=cid, body=copy_of).execute()
    (data / "sync" / "calendar_map.json").unlink()
    result = _sync(fake)
    assert result.counts["deleted"] == 1 and result.counts["created"] == 0
    assert _ids(fake).count("deadline:booth-mif") == 1 and len(_ids(fake)) == 30


def test_events_without_a_hub_id_are_never_touched(data: Path, fake: FakeGoogle) -> None:
    _sync(fake)
    cid = _calendar_id()
    mine = fake.calendar().events().insert(calendarId=cid, body={"summary": "Dentist"}).execute()
    _edit(data / "programmes" / "princeton-mfin.yaml", lambda d: d.update(status="submitted"))
    _sync(fake)
    assert fake.events[cid][mine["id"]]["status"] == "confirmed"


@pytest.mark.parametrize("how", ["gone (404)", "deleted on the phone (cancelled)"])
def test_a_mapped_event_that_is_gone_is_inserted_again(data: Path, fake: FakeGoogle, how: str) -> None:
    _sync(fake)
    cid = _calendar_id()
    old = _event(fake, "essay_final:booth-mif")["id"]
    if how.startswith("gone"):
        fake.remove_entirely(cid, old)
    else:
        fake.delete_by_hand(cid, old)
    result = _sync(fake)
    assert result.counts["created"] == 1 and result.counts["unchanged"] == 29
    new = _event(fake, "essay_final:booth-mif")["id"]
    assert new != old and _map(data)["essay_final:booth-mif"]["event_id"] == new
    assert not any(
        m == "events.patch" and kw["eventId"] == old for m, kw in fake.calls
    )  # never patched in place
    assert _ids(fake).count("essay_final:booth-mif") == 1
    assert fake.count("calendars.insert") == 1  # an event 404 never recreates the calendar (Q8)


@pytest.mark.parametrize("status", [404, 410])
def test_a_patch_on_an_event_deleted_meanwhile_inserts_it_again(
    data: Path, fake: FakeGoogle, status: int
) -> None:
    _sync(fake)
    _edit(data / "programmes" / "booth-mif.yaml", lambda d: d.update(name="Chicago Booth MiF (renamed)"))
    fake.fail("events.patch", status)  # the first patch finds the event gone
    result = _sync(fake)
    assert result.counts["errors"] == 0 and result.counts["created"] == 1
    assert len(_ids(fake)) == len(set(_ids(fake))) == 30
    assert fake.count("calendars.insert") == 1


@pytest.mark.parametrize("status", [429, 500, 503])
def test_a_temporary_error_is_retried_with_backoff(data: Path, fake: FakeGoogle, status: int) -> None:
    sleeps: list[float] = []
    fake.fail("events.insert", status, status)
    result = _sync(fake, sleeps=sleeps)
    assert result.counts["created"] == 30 and result.counts["errors"] == 0
    assert sleeps == [1, 2]  # config google.backoff_s
    assert fake.count("events.insert") == 32


def test_a_persistent_error_is_counted_and_the_run_goes_on(data: Path, fake: FakeGoogle) -> None:
    fake.fail("events.insert", 500, 500, 500)  # google.max_retries = 3 tries
    result = _sync(fake)
    assert result.counts["errors"] == 1 and result.counts["created"] == 29
    assert len(_map(data)) == 29
    assert _sync(fake).counts["created"] == 1  # the next run finishes it
    assert len(_ids(fake)) == 30


def test_a_client_error_is_not_retried(data: Path, fake: FakeGoogle) -> None:
    sleeps: list[float] = []
    fake.fail("events.insert", 400)
    result = _sync(fake, sleeps=sleeps)
    assert result.counts["errors"] == 1 and sleeps == []


def test_dry_run_calls_nothing(data: Path, fake: FakeGoogle) -> None:
    result = _sync(fake, dry_run=True)
    assert result.counts == {"created": 30, "updated": 0, "deleted": 0, "unchanged": 0, "errors": 0}
    assert fake.calls == [] and not (data / "sync" / "calendar_map.json").exists() and _calendar_id() == ""
    _sync(fake)
    _edit(
        data / "programmes" / "princeton-mfin.yaml",
        lambda d: d["deadlines"][0].update(due_local="2026-12-20T23:59"),
    )
    calls = len(fake.calls)
    before = (data / "sync" / "calendar_map.json").read_bytes()
    result = _sync(fake, dry_run=True)
    assert result.counts == {"created": 0, "updated": 12, "deleted": 0, "unchanged": 18, "errors": 0}
    assert len(fake.calls) == calls and (data / "sync" / "calendar_map.json").read_bytes() == before


def test_only_the_stored_calendar_is_ever_touched(data: Path, fake: FakeGoogle) -> None:
    _sync(fake)
    _edit(data / "programmes" / "princeton-mfin.yaml", lambda d: d.update(status="submitted"))
    _sync(fake)
    cid = _calendar_id()
    assert {kw.get("calendarId") for m, kw in fake.calls if m != "calendars.insert"} == {cid}
    assert fake.events["primary"] == {}


def test_the_calendar_is_recreated_only_when_it_is_gone(data: Path, fake: FakeGoogle, caplog) -> None:  # noqa: ANN001
    """DECISIONS #106 Q8: only ``calendars.get`` on the stored id returning 404 recreates it, once, logged."""
    _sync(fake)
    old = _calendar_id()
    del fake.calendars[old]  # the owner deleted "Applications" in Google Calendar
    with caplog.at_level(logging.INFO, logger="hub"):
        result = _sync(fake)
    new = _calendar_id()
    assert new != old and fake.count("calendars.insert") == 2
    assert result.counts["created"] == 30 and len(_map(data)) == 30
    assert any("recreated" in r.getMessage() for r in caplog.records)


# ---- Phase 3 approval additions (DECISIONS #106) ---------------------------------------------------------


def test_a_reused_professor_id_never_leaves_a_stale_follow_up(data: Path, fake: FakeGoogle) -> None:
    """#43 L5: example-a is deleted and a new professor gets the same id; the follow-up names the new one."""
    _sync(fake)
    (data / "professors" / "example-a.yaml").unlink()
    (data / "professors" / "example-a.yaml").write_text(
        "id: example-a\nname: Dr. Z. Other\nuniversity: Other University\n"
        "letters:\n  princeton-mfin:\n    status: requested\n    requested_on: 2026-11-01\n",
        encoding="utf-8",
    )
    _sync(fake)
    follow = [e for e in fake.live_events(_calendar_id()) if "follow up" in e["summary"]]
    assert [e["summary"] for e in follow] == ["[Hub] Princeton MFin: follow up with Dr. Z. Other"]
    assert follow[0]["start"]["dateTime"] == "2026-11-08T09:00:00"
    assert len(_ids(fake)) == 30


def test_a_new_reminder_zone_moves_every_rule_event_without_duplicates(data: Path, fake: FakeGoogle) -> None:
    """Back in Rotterdam in January: settings.reminder_tz changed by hand, every rule event at 09:00 there."""
    _sync(fake)
    _edit(data / "settings.yaml", lambda d: d.update(reminder_tz="Europe/Amsterdam"))
    result = _sync(fake)
    assert result.counts["updated"] == 26 and result.counts["created"] == 0  # 30 − 3 deadlines − 1 interview
    events = fake.live_events(_calendar_id())
    assert len(events) == 30
    rules = [
        e
        for e in events
        if not e["extendedProperties"]["private"][HUB_ID].startswith(("deadline:", "interview:"))
    ]
    assert {(e["start"]["timeZone"], e["start"]["dateTime"][11:]) for e in rules} == {
        ("Europe/Amsterdam", "09:00:00")
    }


# ---- what is kept and logged -------------------------------------------------------------------------


def test_the_last_sync_is_recorded(data: Path, fake: FakeGoogle) -> None:
    _sync(fake)
    last = json.loads((data / "sync" / "last_sync.json").read_text(encoding="utf-8"))
    assert last["created"] == 30 and last["dry_run"] is False and last["error"] is None
    assert last["synced_at"].startswith("2026-10-30T")


def test_the_log_has_counts_and_ids_never_descriptions(data: Path, fake: FakeGoogle, caplog) -> None:  # noqa: ANN001
    with caplog.at_level(logging.DEBUG, logger="hub"):
        _sync(fake)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "created 30" in text
    assert "Prof. A. Example" not in text and "Source:" not in text and "Letters:" not in text


def test_an_unreadable_data_file_stops_the_sync(data: Path, fake: FakeGoogle) -> None:
    """Its reminders would look resolved and their events deleted: refuse, call nothing (fail closed)."""
    _sync(fake)
    calls = len(fake.calls)
    (data / "programmes" / "booth-mif.yaml").write_text("id: booth-mif\nnot_a_field: 1\n", encoding="utf-8")
    result = _sync(fake)
    assert result.error and "programmes/booth-mif.yaml" in result.error
    assert len(fake.calls) == calls and len(_ids(fake)) == 30


def test_a_task_whose_date_moves_into_the_past_leaves_the_calendar(data: Path, fake: FakeGoogle) -> None:
    """A deadline moved earlier (15 → 10 Dec) puts its letter request on 29 Oct, before today: the event is
    deleted, never left on a past date (docs/02 §5.1); the dashboard and digest show it overdue (#106 Q2)."""
    _sync(fake)
    _edit(
        data / "programmes" / "princeton-mfin.yaml",
        lambda d: d["deadlines"][0].update(due_local="2026-12-10T23:59"),
    )
    result = _sync(fake)
    assert result.counts["deleted"] == 1 and result.counts["updated"] == 11
    assert "letters_request_due:princeton-mfin" not in _ids(fake)
