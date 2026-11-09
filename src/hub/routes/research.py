"""Research a programme (US-16) and re-research one (US-18): docs/03 §1, docs/04 §7, §7.1.

Steps, each a normal form POST (no-JS works). Research can take minutes, so the Claude Code call runs in a
worker thread (``run_in_threadpool``) and the app stays responsive:

1. **Run** the ``/research-programme`` skill (the only step that calls Claude Code).
2. **Confirm the official domains**: proposed by the skill for a new programme, prefilled from the stored
   source hosts for a programme without any. Nothing is checked before the user confirms them.
3. **Preview** (new) or **comparison** (re-research): every check runs in Python (``hub.research``).
4. **Save**: a new programme is created exclusively (never over a file); re-research saves only the
   accepted rows, and only if the file hasn't changed since the comparison.

Between steps the skill's output travels in a hidden form field; every check runs again on each POST, so
an edited field gains nothing. Nothing is written before Save, apart from the usage line of the call.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from starlette.concurrency import run_in_threadpool

from hub import research
from hub.ai.skills import SkillError, load_schema, run_skill
from hub.models.programme import Programme
from hub.store import DataError, Snapshot
from hub.views import DeadlineView, InterviewView, summarise
from hub.web import Page, Web, see_other

DEADLINE_FIELDS = ("label", "date", "time", "tz", "time_known", "source_url")
EMPTY_QUERY = 'Enter a programme to research, for example "Berkeley Haas MFE". Nothing was run.'
BAD_URL = "The official page must be a web address starting with http:// or https://. Nothing was run."
BAD_PROPOSAL = (
    "The research result in this form could not be read ({detail}). Run research again. Nothing was saved."
)
FILE_CHANGED = "The file changed since the comparison. Nothing was saved. Run Re-research again."
NOTHING_ACCEPTED = "No change was accepted, so nothing was saved."
KIND_LABELS = {
    "new": "New",
    "changed": "Changed",
    "unchanged": "Unchanged",
    "kept": "Not found by research, kept",
}


def build_router(web: Web) -> APIRouter:
    router = APIRouter()
    store = web.store
    config = web.config
    shared = config.research.official_shared_domains
    web.templates.env.globals["no_official_source"] = research.NO_OFFICIAL_SOURCE
    web.templates.env.globals["config_shared"] = shared
    # "Stops after N minutes" in the busy notice: the research timeout, rounded up to whole minutes
    web.templates.env.globals["research_limit_minutes"] = -(-config.claude.research_timeout_s // 60)

    # ---- helpers ---------------------------------------------------------------------------------

    def find(snapshot: Snapshot, pid: str) -> Programme:
        programme = next((p for p in snapshot.programmes if p.id == pid), None)
        if programme is None:
            if any(e.path == f"programmes/{pid}.yaml" for e in snapshot.errors):
                raise HTTPException(409, f"The file programmes/{pid}.yaml could not be loaded; fix it first.")
            raise HTTPException(404)
        return programme.model_copy(deep=True)

    def call(payload: dict[str, Any], action: str, subject: dict[str, Any]) -> tuple[dict | None, str | None]:
        """Run the skill; (output, None) or (None, the message for the user)."""
        try:
            result = run_skill(
                "research-programme",
                payload,
                load_schema("research_programme"),
                config=config,
                action=action,
                subject=subject,
                source="app",
                timeout_s=config.claude.research_timeout_s,
            )
        except SkillError as err:
            return None, err.message
        try:
            return research.validate_output(result.data), None
        except ValueError as err:
            return None, f"Claude Code returned output the app could not use ({err}). Nothing was saved."

    def parse(form: Any) -> dict[str, Any]:
        """The proposal carried in the form, checked again; ``ValueError`` with the user's message."""
        try:
            output = json.loads(str(form.get("proposal", "")))
        except json.JSONDecodeError:
            raise ValueError(BAD_PROPOSAL.format(detail="not JSON")) from None
        try:
            return research.validate_output(output)
        except ValueError as err:
            raise ValueError(BAD_PROPOSAL.format(detail=str(err))) from None

    def deadline_rows(form: Any) -> list[dict[str, str]] | None:
        """The preview's deadline rows ("deadline-<i>-<field>"), or None if the form has none."""
        rows: dict[int, dict[str, str]] = {}
        for key, value in form.multi_items():
            parts = key.split("-", 2)
            if (
                len(parts) == 3
                and parts[0] == "deadline"
                and parts[1].isdigit()
                and parts[2] in DEADLINE_FIELDS
            ):
                rows.setdefault(int(parts[1]), {})[parts[2]] = str(value)
        return [rows[i] for i in sorted(rows)] if rows else None

    def with_edits(output: dict[str, Any], form: Any) -> dict[str, Any]:
        rows = deadline_rows(form)
        if rows is None:
            return output
        return research.apply_deadline_edits(output, rows, config.time.assumed_deadline_time)

    def preview_deadlines(output: dict[str, Any]) -> list[dict[str, Any]]:
        """Deadline rows for the preview form: the researched ones, or one empty row (DECISIONS #13)."""
        rows = []
        for d in output["deadlines"]:
            date, _, time = str(d["due_local"]).partition("T")
            rows.append({**d, "date": date, "time": time if d["time_known"] else ""})
        return rows or [{"label": "", "date": "", "time": "", "tz": "", "time_known": True, "source_url": ""}]

    def new_page() -> Page:
        return Page("programmes", "/programmes/new", "Programmes", "Research a programme")

    def render_new(request: Request, status: int = 200, **extra: Any) -> HTMLResponse:
        snapshot = store.load_all()
        extra.setdefault("mode", "start")
        extra.setdefault("error", None)
        extra.setdefault("query", "")
        extra.setdefault("url", "")
        return web.render(
            request,
            "pages/research.html",
            new_page(),
            snapshot,
            status_code=status,
            target=None,
            action="/programmes/research",
            **extra,
        )

    def render_preview(
        request: Request,
        output: dict[str, Any],
        domains: list[str],
        status: int = 200,
        error: str | None = None,
    ) -> HTMLResponse:
        proposal = research.check_proposal(output, domains, shared)
        return render_new(
            request,
            status,
            mode="preview",
            proposal=proposal,
            proposal_json=json.dumps(output),
            domains_text="\n".join(domains),
            deadlines=preview_deadlines(output),
            error=error,
        )

    def render_domains(
        request: Request,
        output: dict[str, Any],
        domains: list[str],
        problems: list[str],
        target: Programme | None,
        status: int = 200,
    ) -> HTMLResponse:
        ctx = {
            "mode": "domains",
            "proposal_json": json.dumps(output),
            "domains_text": "\n".join(domains),
            "domain_problems": problems,
            "warnings": output.get("warnings", []),
        }
        if target is None:
            return render_new(request, status, **ctx)
        snapshot = store.load_all()
        return web.render(
            request,
            "pages/research.html",
            reresearch_page(target),
            snapshot,
            status_code=status,
            target=target,
            action=f"/programmes/{target.id}/research",
            error=None,
            **ctx,
        )

    def reresearch_page(p: Programme) -> Page:
        return Page("programmes", f"/programmes/{p.id}", "Programmes", f"Re-research {p.short_name}")

    def render_compare(
        request: Request,
        p: Programme,
        output: dict[str, Any],
        domains: list[str],
        status: int = 200,
        error: str | None = None,
        notice: str | None = None,
        ticked: set[str] | None = None,
        digest: str | None = None,
    ) -> HTMLResponse:
        """The comparison; ``ticked`` rows are rendered ticked (only rows that can be accepted), ``digest``
        carries the comparison's file digest through Accept all / Clear all (the file's own otherwise)."""
        snapshot = store.load_all()
        changes = research.compare(p, output, domains, shared)
        ticked = {c.id for c in changes if c.acceptable and c.id in (ticked or set())}
        return web.render(
            request,
            "pages/research_compare.html",
            reresearch_page(p),
            snapshot,
            status_code=status,
            p=p,
            changes=changes,
            kinds=KIND_LABELS,
            refused=[c.refused for c in changes if c.refused],
            proposal_json=json.dumps(output),
            domains_text="\n".join(domains),
            digest=digest if digest is not None else store.programme_digest(p.id),
            warnings=output.get("warnings", []),
            ticked=ticked,
            accept_all=set(research.accept_all_ids(changes)),
            selection=research.selection_text(changes, ticked),
            error=error,
            notice=notice,
        )

    def render_programme_error(request: Request, pid: str, message: str) -> HTMLResponse:
        snapshot = store.load_all()
        p = find(snapshot, pid)
        return web.render(
            request,
            "pages/programme.html",
            Page("programmes", f"/programmes/{p.id}", "Programmes", p.name),
            snapshot,
            status_code=409,
            p=p,
            summary=summarise(p, snapshot.professors, web.clock(), config),
            reminder_counts=web.reminder_counts(snapshot, p),
            interviews=[
                InterviewView.of(i) for i in sorted(p.interviews, key=lambda i: (i.start_local, i.id))
            ],
            deadline_views=sorted((DeadlineView.of(p, d) for d in p.deadlines), key=lambda d: d.utc),
            interview_form=None,
            interview_error=None,
            verify_error=None,
            card_error=None,
            focus=None,
            research_error=message,
        )

    # ---- new programme (US-16) -------------------------------------------------------------------

    @router.post("/programmes/research", response_class=HTMLResponse)
    async def research_new(request: Request) -> Response:
        form = await request.form()
        if form.get("step") == "check":
            try:
                output = parse(form)
            except ValueError as err:
                return render_new(request, 409, error=str(err))
            domains, problems = research.check_domains(research.split_domains(str(form.get("domains", ""))))
            if problems:
                return render_domains(request, output, domains or [], problems, None)
            return render_preview(request, with_edits(output, form), domains)
        query = str(form.get("query", "")).strip()
        url = str(form.get("url", "")).strip()
        if not query:
            return render_new(request, 409, error=EMPTY_QUERY, url=url)
        if url and not url.lower().startswith(("http://", "https://")):
            return render_new(request, 409, error=BAD_URL, query=query, url=url)
        today = web.today(store.load_all())
        output, error = await run_in_threadpool(
            call, research.research_payload(query, url or None, today), "research_programme", {}
        )
        if output is None:
            return render_new(request, 409, error=error, query=query, url=url)
        return render_domains(request, output, output["official_domains"], [], None)

    @router.post("/programmes/research/save", response_class=HTMLResponse)
    async def research_save(request: Request) -> Response:
        form = await request.form()
        try:
            output = with_edits(parse(form), form)
        except ValueError as err:
            return render_new(request, 409, error=str(err))
        domains, problems = research.check_domains(research.split_domains(str(form.get("domains", ""))))
        if problems:
            return render_domains(request, output, domains, problems, None, status=409)
        snapshot = store.load_all()
        taken = {p.id for p in snapshot.programmes} | {
            e.path.removeprefix("programmes/").removesuffix(".yaml") for e in snapshot.errors
        }
        folder = store.data_dir / "programmes"
        if folder.is_dir():
            taken |= {f.stem.casefold() for f in folder.glob("*.yaml")}
        try:
            programme = research.build_programme(
                output, domains, taken_ids=taken, today=web.today(snapshot), shared=shared
            )
        except ValueError:
            return render_preview(
                request,
                output,
                domains,
                status=409,
                error="Nothing was saved. Please fix the problems below.",
            )
        try:
            store.create_programme(programme)
        except DataError as err:
            return render_preview(
                request, output, domains, status=409, error=f"Nothing was saved. {err.path} {err.message}"
            )
        return see_other(f"/programmes/{programme.id}")

    # ---- re-research (US-18) ---------------------------------------------------------------------

    @router.post("/programmes/{pid}/research", response_class=HTMLResponse)
    async def reresearch(request: Request, pid: str) -> Response:
        form = await request.form()
        snapshot = store.load_all()
        p = find(snapshot, pid)
        if form.get("step") == "check":
            try:
                output = parse(form)
            except ValueError as err:
                return render_programme_error(request, pid, str(err))
            domains, problems = research.check_domains(research.split_domains(str(form.get("domains", ""))))
            if problems:
                return render_domains(request, output, domains, problems, p)
            return render_compare(request, p, output, domains)
        payload = research.reresearch_payload(p, web.today(snapshot))
        output, error = await run_in_threadpool(call, payload, "reresearch_programme", {"programme_id": p.id})
        if output is None:
            return render_programme_error(request, pid, error or "")
        if not p.official_domains:
            return render_domains(request, output, research.prefill_domains(p), [], p)
        return render_compare(request, p, output, list(p.official_domains))

    @router.post("/programmes/{pid}/research/select", response_class=HTMLResponse)
    async def reresearch_select(request: Request, pid: str) -> Response:
        """Accept all changes / Clear all without JS (DECISIONS #68): the comparison again with those rows
        ticked. Never calls Claude and never writes; the comparison's digest is carried through, so Save
        still refuses a file changed since the comparison."""
        form = await request.form()
        snapshot = store.load_all()
        p = find(snapshot, pid)
        try:
            output = parse(form)
        except ValueError as err:
            return render_programme_error(request, pid, str(err))
        domains, problems = research.check_domains(research.split_domains(str(form.get("domains", ""))))
        if problems:
            return render_domains(request, output, domains, problems, p, status=409)
        ticked: set[str] = set()
        if form.get("select") == "all":
            changes = research.compare(p, output, domains, shared)
            ticked = {str(a) for a in form.getlist("accept")} | set(research.accept_all_ids(changes))
        return render_compare(request, p, output, domains, ticked=ticked, digest=str(form.get("digest", "")))

    @router.post("/programmes/{pid}/research/apply", response_class=HTMLResponse)
    async def reresearch_apply(request: Request, pid: str) -> Response:
        form = await request.form()
        snapshot = store.load_all()
        p = find(snapshot, pid)
        try:
            output = parse(form)
        except ValueError as err:
            return render_programme_error(request, pid, str(err))
        domains, problems = research.check_domains(research.split_domains(str(form.get("domains", ""))))
        if problems:
            return render_domains(request, output, domains, problems, p, status=409)
        if form.get("digest") != store.programme_digest(pid):
            return render_compare(request, p, output, domains, status=409, error=FILE_CHANGED)
        accepted = {str(a) for a in form.getlist("accept")}
        try:
            updated = research.apply(p, output, domains, accepted, web.today(snapshot), shared)
        except ValueError as err:
            return render_compare(request, p, output, domains, status=409, error=f"Nothing was saved. {err}")
        if updated is None:
            return render_compare(request, p, output, domains, notice=NOTHING_ACCEPTED)
        try:
            store.save_programme(updated)
        except DataError as err:
            return render_compare(
                request, p, output, domains, status=409, error=f"Nothing was saved. {err.path} {err.message}"
            )
        return see_other(f"/programmes/{pid}")

    return router
