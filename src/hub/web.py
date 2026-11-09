"""Rendering helpers shared by every route: the page chrome, the time filters and HTMX handling.

Every time on every page goes through the filters registered here (``when`` for the display zone,
``school_time`` for the school's own zone) or the macros in ``templates/_time.html`` that use them, so
no template can print a time without its zone (docs/02 §1, C-1-01).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import available_timezones

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from jinja2.runtime import Context

from hub.config import Config
from hub.programme_form import field_label
from hub.rules.timezones import format_date, format_instant, format_today, local_today
from hub.security import allowed_hosts
from hub.store import Snapshot, Store
from hub.views import LABELS

PACKAGE_DIR = Path(__file__).resolve().parent
ALL_ZONES = tuple(sorted(z for z in available_timezones() if "/" in z or z == "UTC"))


@dataclass(frozen=True)
class Page:
    """One page: sidebar key (for aria-current), title shown as h1, and layout options."""

    key: str
    href: str
    label: str
    title: str
    empty_text: str = ""
    wide: bool = False


REMINDER_CATEGORY = {
    "letters": "Letters",
    "documents": "Documents",
    "essays": "Essays",
    "deadline": "Deadline",
    "interview": "Interview",
}


class Web:
    """Templates plus the per-request context every page needs (display zone, date, nav, errors)."""

    def __init__(
        self, config: Config, store: Store, clock: Callable[[], datetime], nav: tuple[Page, ...]
    ) -> None:
        self.config = config
        self.store = store
        self.clock = clock
        self.nav = nav
        self.templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
        env = self.templates.env
        env.filters["when"] = self._when
        env.filters["school_time"] = self._school_time
        env.filters["fdate"] = self._fdate
        env.filters["form_date"] = lambda v: v.strftime("%Y-%m-%d") if v else ""
        env.filters["form_time"] = lambda v: v.strftime("%H:%M") if v else ""
        env.globals["labels"] = LABELS
        env.globals["all_zones"] = ALL_ZONES
        env.globals["field_label"] = field_label
        env.globals["reminder_category"] = REMINDER_CATEGORY  # the dashboard chip (docs/03 §4.1)

    # ---- filters ---------------------------------------------------------------------------------

    @pass_context
    def _when(self, ctx: Context, utc: datetime, style: str = "long") -> str:
        """An instant in the display zone with its abbreviation (and place, e.g. "(Rotterdam)")."""
        return format_instant(utc, ctx["display_tz"], self.config.time, style, with_place=True)

    def _school_time(self, utc: datetime, tz: str, style: str = "long") -> str:
        """An instant in the school's own zone, e.g. "7 Jan 2027, 11:59 pm CT" (never a place name)."""
        return format_instant(utc, tz, self.config.time, style)

    def _fdate(self, value: date | None) -> str:
        return format_date(value, self.config.time) if value else ""

    # ---- context ---------------------------------------------------------------------------------

    def display_tz(self, snapshot: Snapshot) -> str:
        return snapshot.settings.display_tz if snapshot.settings else self.config.time.default_display_tz

    def reminder_counts(self, snapshot: Snapshot, programme: Any) -> tuple[int, int]:
        """(upcoming, overdue) reminders of a programme for its page (``views.reminder_counts``)."""
        from hub.views import reminder_counts

        rtz = snapshot.settings.reminder_tz if snapshot.settings else self.display_tz(snapshot)
        return reminder_counts(
            programme, snapshot.professors, rtz, self.today(snapshot), self.clock(), self.config
        )

    def today(self, snapshot: Snapshot) -> date:
        """Today's date in the display zone: used to stamp dates the user sets (documents, verify)."""
        return local_today(self.clock(), self.display_tz(snapshot))

    def context(self, page: Page, snapshot: Snapshot, **extra: Any) -> dict[str, Any]:
        display_tz = self.display_tz(snapshot)
        return {
            "page": page,
            "nav": self.nav,
            "display_zones": self.config.time.display_zones,
            "display_tz": display_tz,
            "today_label": format_today(self.clock(), display_tz),
            "calendar_name": self.config.google.calendar_name,
            "last_sync": "never",  # filled from the sync state in Phase 3
            "data_errors": snapshot.errors,
            "now": self.clock(),
            **extra,
        }

    def render(
        self,
        request: Request,
        template: str,
        page: Page,
        snapshot: Snapshot,
        status_code: int = 200,
        **extra: Any,
    ) -> HTMLResponse:
        return self.templates.TemplateResponse(
            request, template, self.context(page, snapshot, **extra), status_code=status_code
        )


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def back_path(request: Request, fallback: str = "/") -> str:
    """The same-origin path the request came from (Referer), for the no-JS redirect back.

    Only a Referer on one of the app's own hosts counts, and only its path and query are used, so a
    redirect can never leave the app.
    """
    try:
        parts = urlsplit(request.headers.get("referer", ""))
    except ValueError:
        return fallback
    if parts.scheme != "http" or parts.netloc not in allowed_hosts(request.url.port or 0):
        return fallback
    path = parts.path
    if not path.startswith("/") or path.startswith("//") or "\\" in path:
        return fallback
    return path + (f"?{parts.query}" if parts.query else "")


def see_other(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)
