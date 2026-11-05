"""The web app (docs/03): FastAPI + Jinja2 templates, bound to 127.0.0.1 only.

Phase 0 provides the shell: sidebar, header (date and zone selector), the DataError banner and an
empty page for every sidebar link. Data is loaded fresh from data_dir on each request.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from hub.config import Config, load_config
from hub.store import Snapshot, Store

PACKAGE_DIR = Path(__file__).resolve().parent
LOG_FILE = Path("logs") / "hub.log"


@dataclass(frozen=True)
class Page:
    """One sidebar destination."""

    key: str
    href: str
    label: str
    title: str
    empty_text: str
    wide: bool = False


PAGES: tuple[Page, ...] = (
    Page("dashboard", "/", "Dashboard", "Dashboard",
         "The dashboard (what is due next, and what is missing) arrives in Phase 1."),
    Page("programmes", "/programmes", "Programmes", "Programmes",
         "The list of programmes, each linking to its page, arrives in Phase 1."),
    Page("recommenders", "/recommenders", "Recommenders", "Recommenders",
         "No professors yet. The recommender sheet and Add professor arrive in Phase 2a.", wide=True),
    Page("essays", "/essays", "Essays and CV", "Essays and CV",
         "No essays or CVs yet. Drafts, versions and exact checks arrive in Phase 4a."),
    Page("biography", "/biography", "Biography bank", "Biography bank",
         "No biography entries yet. The biography bank arrives in Phase 4a."),
    Page("settings", "/settings", "Settings", "Settings",
         "Owner details, display zone and the Google connection arrive in later phases."),
)  # fmt: skip
NOT_FOUND = Page("not-found", "", "", "Page not found", "There is no page at this address.")


def _today_label(now: datetime, tz: str) -> str:
    """Header date in the display zone, e.g. "Friday 30 October 2026" (no zero padding on any OS)."""
    local = now.astimezone(ZoneInfo(tz))
    return f"{local:%A} {local.day} {local:%B %Y}"


def _configure_logging(config: Config) -> None:
    """Rotating log in data_dir/logs/hub.log; never logs payloads (essays, emails, descriptions)."""
    logger = logging.getLogger("hub")
    for handler in [h for h in logger.handlers if getattr(h, "_hub_handler", False)]:
        logger.removeHandler(handler)
        handler.close()
    if not config.storage.data_dir.is_dir():
        return
    path = config.storage.data_dir / LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path,
        maxBytes=config.storage.log_max_bytes,
        backupCount=config.storage.log_backup_count,
        encoding="utf-8",
    )
    handler._hub_handler = True  # type: ignore[attr-defined]
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def create_app(config: Config | None = None, clock: Callable[[], datetime] | None = None) -> FastAPI:
    """Build the app. ``clock`` returns the current aware time; tests pass a fixed one."""
    config = config or load_config()
    clock = clock or (lambda: datetime.now(UTC))
    _configure_logging(config)

    app = FastAPI(title="Application Hub", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
    store = Store.from_config(config)

    def render(request: Request, page: Page, snapshot: Snapshot, status_code: int = 200) -> HTMLResponse:
        display_tz = snapshot.settings.display_tz if snapshot.settings else config.time.default_display_tz
        context = {
            "page": page,
            "nav": PAGES,
            "display_zones": config.time.display_zones,
            "display_tz": display_tz,
            "today_label": _today_label(clock(), display_tz),
            "calendar_name": config.google.calendar_name,
            "last_sync": "never",  # filled from the sync state in Phase 3
            "data_errors": snapshot.errors,
        }
        return templates.TemplateResponse(request, "pages/empty.html", context, status_code=status_code)

    def add_page(page: Page) -> None:
        async def view(request: Request) -> HTMLResponse:
            return render(request, page, store.load_all())

        app.add_api_route(page.href, view, methods=["GET"], response_class=HTMLResponse, name=page.key)

    for page in PAGES:
        add_page(page)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> HTMLResponse:
        page = (
            NOT_FOUND
            if exc.status_code == 404
            else Page("error", "", "", "Something went wrong", str(exc.detail))
        )
        return render(request, page, store.load_all(), status_code=exc.status_code)

    return app
