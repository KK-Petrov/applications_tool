"""Recommender routes (docs/03 §1, §4.3–4.4): sheet, status cells, add/edit, detail panel, CSV import.

Every write loads the professor fresh from data_dir, changes it and saves it through the store (atomic,
comment-preserving). HTMX requests get the changed row (plus the totals strip, out of band); plain form
posts are redirected back (no-JS fallback). The sheet never renders a professor's email; the detail
panel does.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from starlette.datastructures import UploadFile

from hub import sheet as sheets
from hub.models.professor import COURSE_DESCRIPTION_MAX_CHARS, LetterStatus, Professor
from hub.professor_form import (
    CSV_OPTIONAL,
    CSV_REQUIRED,
    FIELD_LABELS,
    LETTER_DATE_LABELS,
    LETTER_DATES,
    OTHER,
    TONES,
    UNIVERSITIES,
    CsvResult,
    ProfessorForm,
    build_professor,
    decode_csv,
    form_from_professor,
    form_from_request,
    message,
    new_form,
    parse_csv,
    removable,
)
from hub.rules import letters
from hub.store import DataError, Snapshot
from hub.web import Page, Web, back_path, is_htmx, see_other

SLUG = re.compile(r"^[a-z0-9-]{2,60}$")
SHEET_PAGE = Page("recommenders", "/recommenders", "Recommenders", "Recommenders", wide=True)


def build_router(web: Web) -> APIRouter:
    router = APIRouter()
    store = web.store
    config = web.config

    # ---- helpers ---------------------------------------------------------------------------------

    def find(snapshot: Snapshot, pid: str) -> Professor:
        if not SLUG.fullmatch(pid):
            raise HTTPException(404)
        professor = next((p for p in snapshot.professors if p.id == pid), None)
        if professor is None:
            if any(e.path == f"professors/{pid}.yaml" for e in snapshot.errors):
                raise HTTPException(409, f"The file professors/{pid}.yaml could not be loaded; fix it first.")
            raise HTTPException(404)
        return professor.model_copy(deep=True)

    def taken_ids(snapshot: Snapshot) -> set[str]:
        folder = store.data_dir / "professors"
        on_disk = {p.stem for p in folder.glob("*")} if folder.is_dir() else set()
        broken = {e.path.removeprefix("professors/").removesuffix(".yaml") for e in snapshot.errors}
        return {p.id for p in snapshot.professors} | on_disk | broken

    def save(professor: Professor, new: bool = False) -> str | None:
        """Save through the store; a message naming the file if nothing could be saved.

        A new professor is created exclusively: never written over an existing file (review H1).
        """
        try:
            if new:
                store.create_professor(professor)
            else:
                store.save_professor(professor)
        except DataError as err:
            where = err.path if err.field is None else f"{err.path} ({err.field})"
            return f"{where} {err.message}"
        return None

    def build_sheet(snapshot: Snapshot, params: dict[str, str]) -> sheets.Sheet:
        return sheets.build(
            snapshot.programmes,
            snapshot.professors,
            web.today(snapshot),
            web.clock(),
            config,
            q=params.get("q", ""),
            filter=sheets.parse_filter(params.get("filter")),
            sort=sheets.parse_sort(params.get("sort")),
        )

    def sheet_url(sheet: sheets.Sheet, **extra: str) -> str:
        """The sheet's own address with its search, filter and sort (and e.g. ``selected``)."""
        params = {"q": sheet.q, "filter": sheet.filter.value, "sort": sheet.sort.value, **extra}
        defaults = {"q": "", "filter": sheets.Filter.ALL.value, "sort": sheets.Sort.NEXT_ACTION.value}
        query = urlencode({k: v for k, v in params.items() if v and v != defaults.get(k)})
        return "/recommenders" + (f"?{query}" if query else "")

    def common(sheet: sheets.Sheet | None = None) -> dict[str, Any]:
        return {
            "letter_status": sheets.LETTER_STATUS,
            "letter_chip": sheets.LETTER_CHIP,
            "filter_labels": sheets.FILTER_LABELS,
            "sort_labels": sheets.SORT_LABELS,
            "sheet_url": sheet_url,
            "sheet": sheet,
        }

    def panel_context(snapshot: Snapshot, professor: Professor, sheet: sheets.Sheet) -> dict[str, Any]:
        by_id = {c.programme.id: c for c in sheet.columns}
        askable = [
            by_id[pid].programme
            for pid, x in professor.letters.items()
            if pid in by_id and x.status in letters.ASKABLE
        ]
        askable.sort(key=lambda p: [c.programme.id for c in sheet.columns].index(p.id))
        return {
            "prof": professor,
            "prof_row": sheets.row(professor, sheet.columns, web.today(snapshot), web.clock(), config),
            "askable": askable,
            "date_labels": LETTER_DATE_LABELS,
        }

    # ---- the sheet -------------------------------------------------------------------------------

    def render_sheet(
        request: Request,
        snapshot: Snapshot,
        params: dict[str, str],
        action_error: str | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        sheet = build_sheet(snapshot, params)
        selected = next((p for p in snapshot.professors if p.id == params.get("selected")), None)
        extra = panel_context(snapshot, selected, sheet) if selected else {}
        imported = params.get("imported", "")
        return web.render(
            request,
            "pages/recommenders.html",
            SHEET_PAGE,
            snapshot,
            status_code=status_code,
            selected=selected,
            imported=int(imported) if imported.isdigit() else None,
            action_error=action_error,
            **common(sheet),
            **extra,
        )

    @router.get("/recommenders", response_class=HTMLResponse)
    async def sheet_page(request: Request) -> HTMLResponse:
        return render_sheet(request, store.load_all(), dict(request.query_params))

    @router.get("/recommenders/{pid}/panel", response_class=HTMLResponse)
    async def panel(request: Request, pid: str) -> HTMLResponse:
        snapshot = store.load_all()
        professor = find(snapshot, pid)
        sheet = build_sheet(snapshot, dict(request.query_params))
        ctx = web.context(SHEET_PAGE, snapshot, **common(sheet), **panel_context(snapshot, professor, sheet))
        return web.templates.TemplateResponse(request, "partials/recommender_panel.html", ctx)

    # ---- status cells ----------------------------------------------------------------------------

    @router.post("/recommenders/{pid}/letters/{programme_id}")
    async def set_letter(request: Request, pid: str, programme_id: str) -> Response:
        """Set one letter's status (US-7): stamps the matching date; HTMX gets the row and the totals."""
        snapshot = store.load_all()
        professor = find(snapshot, pid)
        if not any(p.id == programme_id for p in snapshot.programmes):
            raise HTTPException(404)
        form = await request.form()
        view = {k: str(form.get(k, "")) for k in ("q", "filter", "sort", "selected")}  # the sheet as shown
        try:
            status = LetterStatus(str(form.get("status", "")))
        except ValueError:
            raise HTTPException(400, "Unknown letter status.") from None
        current = professor.letters.get(programme_id)
        if current is None or current.status != status:
            professor.letters[programme_id] = letters.set_status(current, status, web.today(snapshot))
            error = save(professor)
            if error:
                if is_htmx(request):
                    raise HTTPException(409, error)
                return render_sheet(request, store.load_all(), view, action_error=error, status_code=409)
        if not is_htmx(request):
            return see_other(back_path(request, "/recommenders"))
        snapshot = store.load_all()
        sheet = build_sheet(snapshot, view)
        professor = find(snapshot, pid)
        selected = professor if view["selected"] == pid else None
        ctx = web.context(
            SHEET_PAGE,
            snapshot,
            **common(sheet),
            r=sheets.row(professor, sheet.columns, web.today(snapshot), web.clock(), config),
            selected=selected,
            focus_cell=programme_id,
            oob_totals=True,
            oob_panel=selected is not None,
            **(panel_context(snapshot, professor, sheet) if selected else {}),
        )
        return web.templates.TemplateResponse(request, "partials/recommender_row.html", ctx)

    @router.post("/recommenders/{pid}/mark-requested")
    async def mark_requested(request: Request, pid: str) -> Response:
        """The panel checklist: ticked programmes whose letter is To ask / Draft ready become requested."""
        snapshot = store.load_all()
        professor = find(snapshot, pid)
        form = await request.form()
        view = {k: str(form.get(k, "")) for k in ("q", "filter", "sort")} | {"selected": pid}
        ticked = {str(v) for v in form.getlist("programmes")}
        changed = False
        for programme_id, letter in professor.letters.items():
            if programme_id in ticked and letter.status in letters.ASKABLE:
                professor.letters[programme_id] = letters.set_status(
                    letter, LetterStatus.REQUESTED, web.today(snapshot)
                )
                changed = True
        if changed:
            error = save(professor)
            if error:
                return render_sheet(request, store.load_all(), view, action_error=error, status_code=409)
        return see_other(sheet_url(build_sheet(store.load_all(), view), selected=pid))

    # ---- add / edit ------------------------------------------------------------------------------

    def render_form(
        request: Request, snapshot: Snapshot, state: ProfessorForm, existing: Professor | None
    ) -> HTMLResponse:
        if existing is None:
            page = Page("recommenders", "/recommenders/new", "Recommenders", "Add professor")
            action = "/recommenders"
        else:
            page = Page(
                "recommenders", f"/recommenders/{existing.id}/edit", "Recommenders", f"Edit {existing.name}"
            )
            action = f"/recommenders/{existing.id}"
        cols = sheets.columns(snapshot.programmes, snapshot.professors, web.clock(), config)
        return web.render(
            request,
            "pages/professor_form.html",
            page,
            snapshot,
            state=state,
            existing=existing,
            action=action,
            columns=cols,
            universities=UNIVERSITIES,
            other=OTHER,
            tones=TONES,
            date_fields=LETTER_DATES,
            date_labels=LETTER_DATE_LABELS,
            field_labels=FIELD_LABELS,
            error_text=lambda f, m: message(f, m) if f in FIELD_LABELS else m,
            askable=letters.ASKABLE,
            removable=removable,
            max_description=COURSE_DESCRIPTION_MAX_CHARS,
            letter_status=sheets.LETTER_STATUS,
        )

    @router.get("/recommenders/new", response_class=HTMLResponse)
    async def new_professor(request: Request) -> HTMLResponse:
        return render_form(request, store.load_all(), new_form(), None)

    @router.get("/recommenders/import", response_class=HTMLResponse)
    async def import_form(request: Request) -> HTMLResponse:
        return render_import(request, store.load_all(), None, "")

    @router.get("/recommenders/{pid}/edit", response_class=HTMLResponse)
    async def edit_professor(request: Request, pid: str) -> HTMLResponse:
        snapshot = store.load_all()
        professor = find(snapshot, pid)
        return render_form(request, snapshot, form_from_professor(professor), professor)

    async def submit(request: Request, existing: Professor | None) -> Response:
        snapshot = store.load_all()
        state = form_from_request(await request.form())
        names = {p.id: p.short_name for p in snapshot.programmes}
        professor = build_professor(state, existing, taken_ids(snapshot), names)
        email = state.values.get("email", "").strip().casefold()
        if existing is not None and existing.email.casefold() == email:
            email = ""  # unchanged: older duplicates stay editable (only a new email is checked)
        twin = next(
            (
                p
                for p in snapshot.professors
                if email and p.email.casefold() == email and (not existing or p.id != existing.id)
            ),
            None,
        )
        if twin is not None:  # one professor per email, as in the CSV import (review L3)
            state.errors.append(("email", f"is already used by {twin.name}"))
            professor = None
        if professor is None:
            return render_form(request, snapshot, state, existing)
        error = save(professor, new=existing is None)
        if error and existing is None and "already exists" in error:
            error += ". Save again to get a new ID."
        if error:
            state.errors = [("file", error)]
            return render_form(request, snapshot, state, existing)
        return see_other(f"/recommenders?selected={professor.id}")

    @router.post("/recommenders")
    async def create(request: Request) -> Response:
        return await submit(request, None)

    @router.post("/recommenders/import")
    async def import_preview(request: Request) -> HTMLResponse:
        snapshot = store.load_all()
        upload = (await request.form()).get("file")
        if not isinstance(upload, UploadFile) or not upload.filename:
            return render_import(request, snapshot, CsvResult([], "Choose a CSV file first."), "")
        text = decode_csv(await upload.read())
        if text is None:
            result = CsvResult([], "The file is not UTF-8 text. In Excel, save it as “CSV UTF-8”.")
            return render_import(request, snapshot, result, "")
        text = text.removeprefix("\ufeff")
        return render_import(
            request, snapshot, parse_csv(text, snapshot.professors, taken_ids(snapshot)), text
        )

    @router.post("/recommenders/import/save")
    async def import_save(request: Request) -> Response:
        snapshot = store.load_all()
        text = str((await request.form()).get("csv_text", ""))
        result = parse_csv(text, snapshot.professors, taken_ids(snapshot))
        if result.file_error or not result.valid:
            return render_import(request, snapshot, result, text)
        saved = 0
        for professor in result.valid:
            error = save(professor, new=True)
            if error:
                total = len(result.valid)
                result.partial = (
                    f"{saved} of {total} professors were imported, then the import stopped: "
                    f"{error.rstrip('.')}. "
                    "Preview the file again to import the rest; the ones already imported are refused "
                    "as existing."
                )
                return render_import(request, store.load_all(), result, text)
            saved += 1
        return see_other(f"/recommenders?imported={saved}")

    def render_import(
        request: Request, snapshot: Snapshot, result: CsvResult | None, text: str
    ) -> HTMLResponse:
        page = Page("recommenders", "/recommenders/import", "Recommenders", "Import professors from CSV")
        return web.render(
            request,
            "pages/recommenders_import.html",
            page,
            snapshot,
            result=result,
            csv_text=text,
            required=CSV_REQUIRED,
            optional=CSV_OPTIONAL,
        )

    @router.post("/recommenders/{pid}")
    async def update(request: Request, pid: str) -> Response:
        return await submit(request, find(store.load_all(), pid))

    return router
