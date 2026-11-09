"""Programme routes (docs/03 §1, §4.1–4.2): list, page, add/edit form, verify, toggles, interviews.

Every write loads the programme fresh from data_dir, changes it and saves it through the store (atomic,
comment-preserving). HTMX requests get the updated card as a partial; plain form posts are redirected
back to the programme page (no-JS fallback).
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError

from hub import standard_items
from hub.models.common import Verification
from hub.models.programme import (
    DocumentStatus,
    Interview,
    InterviewKind,
    InterviewStatus,
    Programme,
)
from hub.programme_form import (
    GROUPS,
    FormState,
    build_programme,
    new_state,
    slugify,
    state_from_form,
    state_from_programme,
    trim_like,
    unique_slug,
)
from hub.store import DataError, Snapshot
from hub.views import VERIFICATION_REASONS, DeadlineView, InterviewView, dashboard, summarise
from hub.web import Page, Web, is_htmx, see_other

SLUG = re.compile(r"^[a-z0-9-]{2,60}$")
FORM_CHANGED = (
    "This programme changed since the form was shown (for example in another tab), so nothing was saved. "
    "The form now shows the stored version; make your change again."
)
PROGRAMMES_PAGE = Page("programmes", "/programmes", "Programmes", "Programmes")
INTERVIEW_LABELS = {
    "kind": "Kind",
    "label": "Label",
    "start_local": "Date and time",
    "tz": "Time zone",
    "status": "Status",
    "notes": "Notes",
}
INTERVIEW_FIELDS = ("interview_id", "kind", "label", "date", "time", "tz", "estimated", "status", "notes")


def _last(form: Any, name: str, default: str = "") -> str:
    """The last value of a field: a checkbox after its hidden default ("0" then "1") wins."""
    values = form.getlist(name)
    return str(values[-1]) if values else default


def build_router(web: Web) -> APIRouter:
    router = APIRouter()
    store = web.store
    config = web.config

    # ---- helpers ---------------------------------------------------------------------------------

    def find(snapshot: Snapshot, pid: str) -> Programme:
        if not SLUG.fullmatch(pid):
            raise HTTPException(404)
        programme = next((p for p in snapshot.programmes if p.id == pid), None)
        if programme is None:
            if any(e.path == f"programmes/{pid}.yaml" for e in snapshot.errors):
                raise HTTPException(409, f"The file programmes/{pid}.yaml could not be loaded; fix it first.")
            raise HTTPException(404)
        return programme.model_copy(deep=True)

    def page_for(p: Programme) -> Page:
        return Page("programmes", f"/programmes/{p.id}", "Programmes", p.name)

    def programme_context(snapshot: Snapshot, p: Programme, **extra: Any) -> dict[str, Any]:
        return {
            "p": p,
            "summary": summarise(p, snapshot.professors, web.clock(), config),
            "interviews": [
                InterviewView.of(i) for i in sorted(p.interviews, key=lambda i: (i.start_local, i.id))
            ],
            "deadline_views": sorted((DeadlineView.of(p, d) for d in p.deadlines), key=lambda d: d.utc),
            "interview_form": None,
            "interview_error": None,
            "verify_error": None,
            "card_error": None,  # (card, message): a failed action, shown inside that card
            "focus": None,  # id of the element to focus after an HTMX swap (keyboard users stay in place)
            "notice": None,  # a whole-page notice: standard items added, a step link dropped (#97)
            "notice_warning": False,
            **extra,
        }

    def render_programme(request: Request, snapshot: Snapshot, p: Programme, **extra: Any) -> HTMLResponse:
        return web.render(
            request, "pages/programme.html", page_for(p), snapshot, **programme_context(snapshot, p, **extra)
        )

    def render_card(
        request: Request, snapshot: Snapshot, p: Programme, card: str, **extra: Any
    ) -> HTMLResponse:
        ctx = web.context(page_for(p), snapshot, **programme_context(snapshot, p, **extra))
        return web.templates.TemplateResponse(request, f"partials/{card}.html", ctx)

    def saved(request: Request, p: Programme, card: str) -> Response:
        """After a successful change: the card partial for HTMX, else a redirect back to the page."""
        if is_htmx(request):
            return render_card(request, store.load_all(), p, card)
        return see_other(f"/programmes/{p.id}")

    def try_save(p: Programme) -> str | None:
        """Save through the store; returns a message for the user if nothing could be saved."""
        try:
            store.save_programme(p)
        except DataError as err:
            where = err.path if err.field is None else f"{err.path} ({err.field})"
            return (
                f"{where} {err.message}"  # names the file, e.g. "programmes/x.yaml could not be written: …"
            )
        return None

    def failed(request: Request, pid: str, card: str, message: str, status: int = 409) -> Response:
        """A failed card action (docs/03 §5): the card re-rendered from what is stored, with the message
        inside it, so the control never shows a value that was not saved."""
        snapshot = store.load_all()
        p = find(snapshot, pid)  # an unreadable file raises → the page-level toast
        if is_htmx(request):
            return render_card(request, snapshot, p, card, card_error=(card, message))
        response = render_programme(request, snapshot, p, card_error=(card, message))
        response.status_code = status
        return response

    # ---- list and page ---------------------------------------------------------------------------

    @router.get("/programmes", response_class=HTMLResponse)
    async def programmes_list(request: Request) -> HTMLResponse:
        snapshot = store.load_all()
        board = dashboard(
            snapshot.programmes, snapshot.professors, web.clock(), config, web.display_tz(snapshot)
        )
        return web.render(request, "pages/programmes.html", PROGRAMMES_PAGE, snapshot, board=board)

    @router.get("/programmes/new", response_class=HTMLResponse)
    async def new_form(request: Request) -> HTMLResponse:
        snapshot = store.load_all()
        return render_form(request, snapshot, new_state(config), None)

    @router.get("/programmes/{pid}", response_class=HTMLResponse)
    async def programme_page(
        request: Request,
        pid: str,
        interview: str | None = None,
        added_steps: int | None = Query(default=None, ge=0, le=1000),
        added_documents: int | None = Query(default=None, ge=0, le=1000),
        notice: str | None = Query(default=None, max_length=64),
    ) -> HTMLResponse:
        snapshot = store.load_all()
        p = find(snapshot, pid)
        form = interview_form_values(p, interview) if interview else None
        dropped = pending.get(notice or "")
        if dropped is not None and dropped[0] == p.id:
            pending.pop(notice or "", None)  # shown once
            return render_programme(
                request, snapshot, p, interview_form=form, notice=dropped[1], notice_warning=True
            )
        return render_programme(
            request, snapshot, p, interview_form=form, notice=added_notice(added_steps, added_documents)
        )

    # ---- standard steps and documents (DECISIONS #97) -------------------------------------------------

    def added_notice(steps: int | None, documents: int | None) -> str | None:
        """What the programme page says after "Add ticked items" (counts from the redirect)."""
        if steps is None and documents is None:
            return None
        parts = [
            f"{n} {noun}{'s' if n != 1 else ''}"
            for n, noun in ((steps or 0, "step"), (documents or 0, "document"))
            if n
        ]
        if not parts:
            return "Nothing was added: every ticked item is already on this programme."
        return "Added " + " and ".join(parts) + "."

    def render_standard(
        request: Request, snapshot: Snapshot, p: Programme, status: int = 200, error: str | None = None
    ) -> HTMLResponse:
        proposals = standard_items.propose(p, config.standard_items)
        page = Page(
            "programmes", f"/programmes/{p.id}/standard", "Programmes", "Add standard steps and documents"
        )
        return web.render(
            request,
            "pages/programme_standard.html",
            page,
            snapshot,
            status_code=status,
            p=p,
            new=[x for x in proposals if x.existing is None],
            there=[x for x in proposals if x.existing is not None],
            error=error,
        )

    @router.get("/programmes/{pid}/standard", response_class=HTMLResponse)
    async def standard_preview(request: Request, pid: str) -> HTMLResponse:
        """The preview: every new standard item ticked, the ones already there listed; nothing written."""
        snapshot = store.load_all()
        return render_standard(request, snapshot, find(snapshot, pid))

    @router.post("/programmes/{pid}/standard", response_class=HTMLResponse)
    async def standard_save(request: Request, pid: str) -> Response:
        """Add the ticked items that are still new, worked out again from the file (never from the form's
        labels), through the store; refused with nothing written for an unknown key or nothing ticked."""
        form = await request.form()
        keys = {str(v) for v in form.getlist("add")}
        snapshot = store.load_all()
        p = find(snapshot, pid)
        if not keys:
            return render_standard(request, snapshot, p, 409, "Nothing was saved: Tick at least one item.")
        try:
            updated, steps, documents = standard_items.apply(p, keys, config.standard_items)
        except standard_items.UnknownItem as err:
            return render_standard(request, snapshot, p, 409, f"Nothing was saved: {err}.")
        if (steps or documents) and (error := try_save(updated)) is not None:
            return render_standard(request, store.load_all(), p, 409, f"Nothing was saved: {error}.")
        return see_other(f"/programmes/{p.id}?added_steps={len(steps)}&added_documents={len(documents)}")

    # ---- add / edit ------------------------------------------------------------------------------

    def render_form(
        request: Request,
        snapshot: Snapshot,
        state: FormState,
        existing: Programme | None,
        digest: str = "",
        status: int = 200,
    ) -> HTMLResponse:
        if existing is None:
            page = Page("programmes", "/programmes/new", "Programmes", "Add programme")
            action = "/programmes"
        else:
            page = Page(
                "programmes", f"/programmes/{existing.id}/edit", "Programmes", f"Edit {existing.short_name}"
            )
            action = f"/programmes/{existing.id}"
        return web.render(
            request,
            "pages/programme_form.html",
            page,
            snapshot,
            state=state,
            existing=existing,
            action=action,
            groups=GROUPS,
            reasons=VERIFICATION_REASONS,
            config_assumed_time=config.time.assumed_deadline_time,
            digest=digest,
            status_code=status,
        )

    @router.get("/programmes/{pid}/edit", response_class=HTMLResponse)
    async def edit_form(request: Request, pid: str) -> HTMLResponse:
        snapshot = store.load_all()
        p = find(snapshot, pid)
        return render_form(request, snapshot, state_from_programme(p), p, store.programme_digest(p.id))

    # one-time page notices after a redirect (post/redirect/get), keyed by a random token; in memory only
    # (a single-user local app: a restart just drops an unread notice; data-integrity r11 F1)
    pending: dict[str, tuple[str, str]] = {}

    def remember_notice(pid: str, text: str) -> str:
        while len(pending) >= 32:
            pending.pop(next(iter(pending)))
        token = secrets.token_urlsafe(9)
        pending[token] = (pid, text)
        return token

    async def submit(request: Request, existing: Programme | None) -> Response:
        snapshot = store.load_all()
        form = await request.form()
        state, add = state_from_form(form)
        digest = str(form.get("digest", ""))
        if add:  # "Add … row": show the form again with one more row; nothing is saved
            return render_form(request, snapshot, state, existing, digest)
        if existing is not None and digest != store.programme_digest(existing.id):
            # the file changed since this form was shown (another tab added standard items, a toggle, a
            # reload re-posting an old form): saving would rebuild steps and documents from stale rows and
            # drop what was added since (data-integrity r11 F1). Refused like re-research's apply; the form
            # shows the stored version.
            current = find(snapshot, existing.id)
            fresh = state_from_programme(current)
            fresh.errors = [("file", FORM_CHANGED)]
            return render_form(request, snapshot, fresh, current, store.programme_digest(current.id), 409)
        taken = {p.id for p in snapshot.programmes} | {
            e.path.removeprefix("programmes/").removesuffix(".yaml") for e in snapshot.errors
        }
        programme, errors = build_programme(state, existing, taken, config)
        if programme is None:
            state.errors = errors
            return render_form(request, snapshot, state, existing, digest)
        if existing is None and (store.data_dir / "programmes" / f"{programme.id}.yaml").exists():
            state.errors = [("id", f"a programme file {programme.id}.yaml already exists; choose another")]
            return render_form(request, snapshot, state, existing, digest)
        try:
            store.save_programme(programme)
        except DataError as err:
            state.errors = [
                (err.field or "file", f"{err.path} {err.message}")
            ]  # header says "Nothing was saved"
            return render_form(request, snapshot, state, existing, digest)
        if existing is not None and (lost := standard_items.dropped_links(existing, programme)):
            # a step lost its link with the requirement removed in this edit: said, never silent (#97); shown
            # after a redirect, so a reload never posts the form again (data-integrity r11 F1)
            token = remember_notice(programme.id, "; ".join(lost) + ".")
            return see_other(f"/programmes/{programme.id}?notice={token}")
        return see_other(f"/programmes/{programme.id}")

    @router.post("/programmes")
    async def create(request: Request) -> Response:
        return await submit(request, None)

    @router.post("/programmes/{pid}")
    async def update(request: Request, pid: str) -> Response:
        return await submit(request, find(store.load_all(), pid))

    # ---- verify (docs/02 §6: the only code path that sets ``verified``) ----------------------------

    @router.post("/programmes/{pid}/verify")
    async def verify(request: Request, pid: str) -> Response:
        snapshot = store.load_all()
        p = find(snapshot, pid)
        if not p.sources:
            return render_programme(
                request, snapshot, p, verify_error="Add a source before marking verified (Edit → Sources)."
            )
        # change only these fields on the loaded model, so the file keeps its shape
        p.verification = Verification.VERIFIED
        p.verified_on = web.today(snapshot)
        if p.verification_note is not None:  # assigning marks the field set; a file without it stays so
            p.verification_note = None
        for d in p.deadlines:
            d.verification = Verification.VERIFIED
        if (error := try_save(p)) is not None:
            stored = store.load_all()  # show what is on disk: still unverified (rule 11, DECISIONS #34)
            response = render_programme(
                request, stored, find(stored, pid), verify_error=f"Nothing was saved. {error}"
            )
            response.status_code = 409
            return response
        return see_other(f"/programmes/{pid}")

    # ---- requirements, steps, documents ----------------------------------------------------------

    @router.post("/programmes/{pid}/requirements/{rid}")
    async def set_requirement(request: Request, pid: str, rid: str) -> Response:
        p = find(store.load_all(), pid)
        item = next((r for r in p.requirements if r.id == rid), None)
        if item is None:
            raise HTTPException(404)
        item.done = _last(await request.form(), "done") == "1"
        if (error := try_save(p)) is not None:
            return failed(request, pid, "requirements", error)
        return saved(request, p, "requirements")

    @router.post("/programmes/{pid}/steps/{order}")
    async def set_step(request: Request, pid: str, order: int) -> Response:
        p = find(store.load_all(), pid)
        item = next((s for s in p.steps if s.order == order), None)
        if item is None:
            raise HTTPException(404)
        item.done = _last(await request.form(), "done") == "1"
        if (error := try_save(p)) is not None:
            return failed(request, pid, "steps", error)
        return saved(request, p, "steps")

    @router.post("/programmes/{pid}/documents/{did}")
    async def set_document(request: Request, pid: str, did: str) -> Response:
        snapshot = store.load_all()
        p = find(snapshot, pid)
        item = next((d for d in p.documents if d.id == did), None)
        if item is None:
            raise HTTPException(404)
        status = _last(await request.form(), "status")
        if status not in DocumentStatus.__members__.values():
            return failed(request, pid, "documents", "Unknown document status.", status=400)
        if item.status != status:
            item.status = DocumentStatus(status)
            # DECISIONS #23, #32: back to "not requested" clears the date; any other change stamps today
            item.updated = None if item.status == DocumentStatus.NOT_REQUESTED else web.today(snapshot)
            if (error := try_save(p)) is not None:
                return failed(request, pid, "documents", error)
        return saved(request, p, "documents")

    # ---- interviews ------------------------------------------------------------------------------

    def interview_form_values(p: Programme, interview_id: str) -> dict[str, str]:
        existing = next((i for i in p.interviews if i.id == interview_id), None)
        if existing is None:
            return dict.fromkeys(INTERVIEW_FIELDS, "") | {"kind": "interview", "status": "scheduled"}
        return {
            "interview_id": existing.id,
            "kind": existing.kind.value,
            "label": existing.label,
            "date": existing.start_local.strftime("%Y-%m-%d"),
            "time": existing.start_local.strftime("%H:%M"),
            "tz": existing.tz,
            "estimated": "1" if existing.estimated else "",
            "status": existing.status.value,
            "notes": existing.notes or "",
        }

    @router.get("/programmes/{pid}/interviews/form", response_class=HTMLResponse)
    async def interview_form(request: Request, pid: str, interview_id: str = "new") -> HTMLResponse:
        snapshot = store.load_all()
        p = find(snapshot, pid)
        return render_card(
            request,
            snapshot,
            p,
            "interviews",
            interview_form=interview_form_values(p, interview_id),
            focus="iv-kind",
        )

    @router.post("/programmes/{pid}/interviews")
    async def save_interview(request: Request, pid: str) -> Response:
        snapshot = store.load_all()
        p = find(snapshot, pid)
        form = await request.form()
        values = {name: _last(form, name).strip() for name in INTERVIEW_FIELDS}
        values["notes"] = _last(form, "notes").replace("\r\n", "\n")
        values["estimated"] = "1" if values["estimated"] == "1" else ""
        error = interview_error(values)
        existing = next((i for i in p.interviews if i.id == values["interview_id"]), None)
        interview: Interview | None = None
        if error is None:
            hour, minute = (int(x) for x in values["time"].split(":"))
            day = datetime.strptime(values["date"], "%Y-%m-%d")
            ids = {i.id for i in p.interviews}
            try:
                interview = Interview(
                    id=existing.id if existing else unique_slug(slugify(values["label"], "interview"), ids),
                    kind=values["kind"],
                    label=values["label"],
                    start_local=day.replace(hour=hour, minute=minute),
                    tz=values["tz"],
                    estimated=values["estimated"] == "1",
                    status=values["status"],
                    notes=values["notes"] or None,
                )
            except ValidationError as exc:
                fields = sorted({INTERVIEW_LABELS.get(str(e["loc"][0]), "a field") for e in exc.errors()})
                error = f"Check {', '.join(fields)}: the value is not valid. Nothing was saved."
        if error is not None or interview is None:
            if is_htmx(request):
                return render_card(
                    request,
                    snapshot,
                    p,
                    "interviews",
                    interview_form=values,
                    interview_error=error,
                    focus="iv-error",
                )
            return render_programme(request, snapshot, p, interview_form=values, interview_error=error)
        if existing is not None:
            # keep a hand-written interview's shape: no defaulted keys it never had (DECISIONS #29b, #33)
            interview = Interview.model_validate(trim_like(interview.model_dump(), existing))
            p.interviews = [interview if i.id == existing.id else i for i in p.interviews]
        else:
            p.interviews = [*p.interviews, interview]
        if (error := try_save(p)) is not None:
            error = f"Nothing was saved. {error}"
            stored = store.load_all()  # list what is on disk; keep what was typed in the form (#34)
            p = find(stored, pid)
            if is_htmx(request):
                return render_card(
                    request,
                    stored,
                    p,
                    "interviews",
                    interview_form=values,
                    interview_error=error,
                    focus="iv-error",
                )
            response = render_programme(request, stored, p, interview_form=values, interview_error=error)
            response.status_code = 409
            return response
        if is_htmx(request):
            return render_card(request, store.load_all(), p, "interviews", focus="iv-add")
        return saved(request, p, "interviews")

    def interview_error(v: dict[str, str]) -> str | None:
        if not v["label"]:
            return "Enter a label for the interview or assessment."
        if v["kind"] not in InterviewKind.__members__.values():
            return "Choose a kind: interview or assessment."
        if v["status"] not in InterviewStatus.__members__.values():
            return "Choose a status."
        try:
            datetime.strptime(v["date"], "%Y-%m-%d")
        except ValueError:
            return "Enter the date (YYYY-MM-DD)."
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v["time"]):
            return "Enter the time as HH:MM (24-hour)."
        try:
            ZoneInfo(v["tz"])
        except (ZoneInfoNotFoundError, ValueError):
            return f"{v['tz'] or 'The time zone'!s} is not an IANA time zone (e.g. America/New_York)."
        return None

    return router
