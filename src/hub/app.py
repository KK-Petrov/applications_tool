"""The web app (docs/03): FastAPI + Jinja2 templates + HTMX, bound to 127.0.0.1 only.

Data is loaded fresh from data_dir on each request. Every request first passes the local-origin
checks (DECISIONS #26). Pages not yet built show an empty state naming the phase that adds them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from hub.config import Config, load_config
from hub.routes.courses import build_router as courses_router
from hub.routes.programmes import build_router as programmes_router
from hub.routes.recommenders import build_router as recommenders_router
from hub.routes.research import build_router as research_router
from hub.routes.usage import USAGE_PAGE
from hub.routes.usage import build_router as usage_router
from hub.security import LocalOriginMiddleware
from hub.store import DataError, Store
from hub.views import dashboard
from hub.web import PACKAGE_DIR, Page, Web, back_path, is_htmx, see_other

LOG_FILE = Path("logs") / "hub.log"

PAGES: tuple[Page, ...] = (
    Page("dashboard", "/", "Dashboard", "Dashboard"),
    Page("programmes", "/programmes", "Programmes", "Programmes"),
    Page("recommenders", "/recommenders", "Recommenders", "Recommenders",
         "No professors yet. The recommender sheet and Add professor arrive in Phase 2a.", wide=True),
    Page("essays", "/essays", "Essays and CV", "Essays and CV",
         "No essays or CVs yet. Drafts, versions and exact checks arrive in Phase 4a."),
    Page("biography", "/biography", "Biography bank", "Biography bank",
         "No biography entries yet. The biography bank arrives in Phase 4a."),
    USAGE_PAGE,  # AI usage audit (DECISIONS #60)
    Page("settings", "/settings", "Settings", "Settings",
         "Owner details, display zone and the Google connection arrive in later phases."),
)  # fmt: skip
PLACEHOLDER_PAGES = {"essays", "biography", "settings"}
NOT_FOUND = Page("not-found", "", "", "Page not found", "There is no page at this address.")


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
    app.state.config = config  # what this app was built with (the e2e guard checks its usage-log paths)
    app.add_middleware(LocalOriginMiddleware)
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
    store = Store.from_config(config)
    web = Web(config, store, clock, PAGES)

    @app.get("/", response_class=HTMLResponse, name="dashboard")
    async def dashboard_page(request: Request) -> HTMLResponse:
        snapshot = store.load_all()
        board = dashboard(snapshot.programmes, snapshot.professors, clock(), config, web.display_tz(snapshot))
        return web.render(request, "pages/dashboard.html", PAGES[0], snapshot, board=board)

    # research first: POST /programmes/research must not reach POST /programmes/{pid}
    app.include_router(research_router(web))
    app.include_router(programmes_router(web))
    app.include_router(courses_router(web))  # before recommenders: /recommenders/course-suggestions
    app.include_router(recommenders_router(web))
    app.include_router(usage_router(web))

    def add_placeholder(page: Page) -> None:
        async def view(request: Request) -> HTMLResponse:
            return web.render(request, "pages/empty.html", page, store.load_all())

        app.add_api_route(page.href, view, methods=["GET"], response_class=HTMLResponse, name=page.key)

    for page in PAGES:
        if page.key in PLACEHOLDER_PAGES:
            add_placeholder(page)

    @app.post("/display-tz")
    async def set_display_tz(request: Request) -> Response:
        """Header zone selector (US-2): saves settings.display_tz; every page re-renders in that zone."""
        zone = str((await request.form()).get("display_tz", ""))
        if zone not in config.display_zone_ids:
            raise HTTPException(400, "Unknown display time zone.")
        try:
            settings = store.load_settings()
            if settings.display_tz != zone:
                settings.display_tz = zone
                store.save_settings(settings)
        except DataError as err:
            raise HTTPException(
                409, f"The display zone could not be saved: {err.path} {err.message}"
            ) from None
        if is_htmx(request):
            return Response(status_code=204, headers={"HX-Refresh": "true"})
        return see_other(back_path(request))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> HTMLResponse:
        if is_htmx(request):  # swapped into #action-error by the htmx-config in base.html
            detail = (
                exc.detail
                if exc.status_code != 404
                else "This item no longer exists or its file could not be loaded."
            )
            return web.templates.TemplateResponse(
                request,
                "partials/action_error.html",
                {"message": detail, "saving": request.method != "GET"},
                status_code=exc.status_code,
            )
        page = (
            NOT_FOUND
            if exc.status_code == 404
            else Page("error", "", "", "Something went wrong", str(exc.detail))
        )
        return web.render(request, "pages/empty.html", page, store.load_all(), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def bad_request(request: Request, exc: RequestValidationError) -> HTMLResponse:
        """A malformed request (only reachable by tampering): a readable message, never FastAPI's JSON."""
        return await http_error(request, StarletteHTTPException(400, "The request was not understood."))

    return app
