"""An in-memory Google Calendar + Gmail service for tests (docs/05 §2.5). Never touches the network.

Same method chain as ``googleapiclient``: ``service.events().insert(calendarId=…, body=…).execute()``. Errors
are real ``googleapiclient.errors.HttpError`` objects, injected per method with ``fail(method, *statuses)``
(each status is raised once, in order, at ``execute()``). ``calls`` logs every executed call as
``(method, kwargs)``. Events deleted "by hand" stay listed with ``status: cancelled`` (as Google returns them
with ``showDeleted``); ``events().get``/``patch`` on a deleted event raise 410, on an unknown id 404.
"""

from __future__ import annotations

import copy
import itertools
from collections import defaultdict, deque
from typing import Any

import httplib2
from googleapiclient.errors import HttpError


def http_error(status: int, reason: str = "") -> HttpError:
    resp = httplib2.Response({"status": status, "reason": reason or "error"})
    return HttpError(resp, f'{{"error": {{"code": {status}, "message": "{reason or "error"}"}}}}'.encode())


class _Request:
    def __init__(self, fake: FakeGoogle, method: str, kwargs: dict[str, Any], run: Any) -> None:
        self.fake, self.method, self.kwargs, self.run = fake, method, kwargs, run

    def execute(self, num_retries: int = 0) -> Any:  # noqa: ARG002 (same signature as googleapiclient)
        self.fake.calls.append((self.method, copy.deepcopy(self.kwargs)))
        queue = self.fake.failures.get(self.method)
        if queue:
            raise http_error(queue.popleft())
        return self.run()


class _Calendars:
    def __init__(self, fake: FakeGoogle) -> None:
        self.fake = fake

    def insert(self, body: dict[str, Any]) -> _Request:
        def run() -> dict[str, Any]:
            cid = f"cal-{next(self.fake.ids)}@group.calendar.google.com"
            self.fake.calendars[cid] = copy.deepcopy(body)
            return {"id": cid, **copy.deepcopy(body)}

        return _Request(self.fake, "calendars.insert", {"body": body}, run)

    def get(self, calendarId: str) -> _Request:  # noqa: N803 (Google's argument name)
        def run() -> dict[str, Any]:
            if calendarId not in self.fake.calendars:
                raise http_error(404, "Not Found")
            return {"id": calendarId, **self.fake.calendars[calendarId]}

        return _Request(self.fake, "calendars.get", {"calendarId": calendarId}, run)

    def delete(self, calendarId: str) -> _Request:  # noqa: N803
        def run() -> str:
            if self.fake.calendars.pop(calendarId, None) is None:
                raise http_error(404, "Not Found")
            self.fake.events.pop(calendarId, None)
            return ""

        return _Request(self.fake, "calendars.delete", {"calendarId": calendarId}, run)


class _Events:
    def __init__(self, fake: FakeGoogle) -> None:
        self.fake = fake

    def _calendar(self, cid: str) -> dict[str, dict[str, Any]]:
        if cid not in self.fake.calendars:
            raise http_error(404, "Not Found")
        return self.fake.events[cid]

    def insert(self, calendarId: str, body: dict[str, Any]) -> _Request:  # noqa: N803
        def run() -> dict[str, Any]:
            eid = f"evt{next(self.fake.ids)}"
            event = {**copy.deepcopy(body), "id": eid, "status": "confirmed"}
            self._calendar(calendarId)[eid] = event
            return copy.deepcopy(event)

        return _Request(self.fake, "events.insert", {"calendarId": calendarId, "body": body}, run)

    def _live(self, cid: str, eid: str) -> dict[str, Any]:
        event = self._calendar(cid).get(eid)
        if event is None:
            raise http_error(404, "Not Found")
        if event["status"] == "cancelled":
            raise http_error(410, "Resource has been deleted")
        return event

    def get(self, calendarId: str, eventId: str) -> _Request:  # noqa: N803
        return _Request(
            self.fake,
            "events.get",
            {"calendarId": calendarId, "eventId": eventId},
            lambda: copy.deepcopy(self._live(calendarId, eventId)),
        )

    def patch(self, calendarId: str, eventId: str, body: dict[str, Any]) -> _Request:  # noqa: N803
        def run() -> dict[str, Any]:
            event = self._live(calendarId, eventId)
            event.update(copy.deepcopy(body))
            return copy.deepcopy(event)

        return _Request(
            self.fake, "events.patch", {"calendarId": calendarId, "eventId": eventId, "body": body}, run
        )

    def delete(self, calendarId: str, eventId: str) -> _Request:  # noqa: N803
        def run() -> str:
            event = self._live(calendarId, eventId)
            event["status"] = "cancelled"  # Google keeps a deleted event, cancelled
            return ""

        return _Request(self.fake, "events.delete", {"calendarId": calendarId, "eventId": eventId}, run)

    def list(
        self, calendarId: str, pageToken: str | None = None, showDeleted: bool = False, **kw: Any
    ) -> _Request:  # noqa: N803
        def run() -> dict[str, Any]:
            items = [
                e for e in self._calendar(calendarId).values() if showDeleted or e["status"] != "cancelled"
            ]
            start = int(pageToken or 0)
            page = items[start : start + self.fake.page_size]
            out: dict[str, Any] = {"items": copy.deepcopy(page)}
            if start + self.fake.page_size < len(items):
                out["nextPageToken"] = str(start + self.fake.page_size)
            return out

        kwargs = {"calendarId": calendarId, "pageToken": pageToken, "showDeleted": showDeleted, **kw}
        return _Request(self.fake, "events.list", kwargs, run)


class _Messages:
    def __init__(self, fake: FakeGoogle) -> None:
        self.fake = fake

    def send(self, userId: str, body: dict[str, Any]) -> _Request:  # noqa: N803
        def run() -> dict[str, Any]:
            self.fake.sent.append({"userId": userId, **copy.deepcopy(body)})
            return {"id": f"msg{next(self.fake.ids)}"}

        return _Request(self.fake, "messages.send", {"userId": userId, "body": body}, run)


class _Users:
    def __init__(self, fake: FakeGoogle) -> None:
        self.fake = fake

    def messages(self) -> _Messages:
        return _Messages(self.fake)


class _CalendarService:
    def __init__(self, fake: FakeGoogle) -> None:
        self.fake = fake

    def calendars(self) -> _Calendars:
        return _Calendars(self.fake)

    def events(self) -> _Events:
        return _Events(self.fake)


class _GmailService:
    def __init__(self, fake: FakeGoogle) -> None:
        self.fake = fake

    def users(self) -> _Users:
        return _Users(self.fake)


class FakeGoogle:
    """One fake Google account: calendars with events, a Gmail outbox, failure queues and a call log."""

    def __init__(self, page_size: int = 50) -> None:
        self.calendars: dict[str, dict[str, Any]] = {"primary": {"summary": "owner@example.com"}}
        self.events: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        self.sent: list[dict[str, Any]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.failures: dict[str, deque[int]] = {}
        self.page_size = page_size
        self.ids = itertools.count(1)

    def calendar(self) -> _CalendarService:
        return _CalendarService(self)

    def gmail(self) -> _GmailService:
        return _GmailService(self)

    def fail(self, method: str, *statuses: int) -> None:
        """Raise these HTTP statuses once each, in order, on the next calls of ``method``."""
        self.failures.setdefault(method, deque()).extend(statuses)

    # ---- test helpers -------------------------------------------------------------------------------

    def live_events(self, calendar_id: str) -> list[dict[str, Any]]:
        return [e for e in self.events[calendar_id].values() if e["status"] != "cancelled"]

    def delete_by_hand(self, calendar_id: str, event_id: str) -> None:
        """The owner deletes the event on the phone: Google keeps it as cancelled."""
        self.events[calendar_id][event_id]["status"] = "cancelled"

    def remove_entirely(self, calendar_id: str, event_id: str) -> None:
        """The event is gone without a trace (404 from then on)."""
        del self.events[calendar_id][event_id]

    def count(self, method: str) -> int:
        return sum(1 for m, _ in self.calls if m == method)
