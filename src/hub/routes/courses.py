"""Courses and course import (US-19 to US-21; docs/03 §1, §4.8; docs/04 §9; DECISIONS #44, #64).

- ``GET /courses``: the minimal list (the biography bank shows courses in full in Phase 4a).
- ``GET/POST /courses/import``: a .docx, a text PDF or pasted text → text → identifiers stripped in Python
  (a label whose value can't be found stops here) → the **review** of exactly the text that would be sent,
  each removed value highlighted (DECISIONS #70). No AI call, nothing written.
- ``POST /courses/import/send``: "Send to Claude" on the review: the text is stripped again (strict) →
  ``/extract-courses`` → every returned field stripped again → the preview, where each grade is checked
  against its own row. Nothing is written: the forms carry only the identifier-stripped text and the rows.
- ``POST /courses/import/save``: Accept (one row) or Accept all writes the rows that pass every check to
  ``courses.yaml`` with ``confirmed: true``; the first accept writes ``sources/<name>.txt``.
- ``POST /courses/import/professors``: every lecturer of the document's confirmed courses once; nothing is
  ticked; with ``step=create`` the ticked ones are created in one batch (never over a file) or linked.
- ``GET /recommenders/course-suggestions?name=``: Add professor's suggestions (courses whose lecturers share
  the typed surname, ignoring case and diacritics).
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from hub.ai.skills import SkillError, load_schema, run_skill
from hub.courses import extract, identifiers, importer, lecturers
from hub.models.professor import Professor
from hub.rules import names
from hub.store import DataError, Snapshot
from hub.web import Page, Web, see_other

COURSES_PAGE = Page("recommenders", "/courses", "Recommenders", "Courses")
IMPORT_PAGE = Page("recommenders", "/courses/import", "Recommenders", "Import courses")
PROFESSORS_PAGE = Page("recommenders", "/courses/import/professors", "Recommenders", "Create professors")
ROW_TEXT = ("name", "code", "institution", "grade", "credits", "term", "lecturers", "description")
NOTHING_GIVEN = "Choose a file or paste the text of your transcript. Nothing was read."
PASTED = "pasted text"


def _lf(text: str) -> str:
    """A browser's CRLF (or a lone CR) as LF, like every multi-line field (#39(8))."""
    return extract.text_from_paste(text)


def build_router(web: Web) -> APIRouter:
    router = APIRouter()
    store = web.store
    config = web.config
    cfg = config.courses
    # the busy notice's limit for a course import: claude.timeout_s in whole minutes, rounded up (#63)
    web.templates.env.globals["import_limit_minutes"] = -(-config.claude.timeout_s // 60)
    web.templates.env.globals["import_max_mb"] = cfg.max_upload_bytes // 1_000_000

    # ---- the list --------------------------------------------------------------------------------

    @router.get("/courses", response_class=HTMLResponse)
    async def courses_list(request: Request) -> HTMLResponse:
        snapshot = store.load_all()
        return web.render(request, "pages/courses.html", COURSES_PAGE, snapshot, courses=snapshot.courses)

    # ---- import ----------------------------------------------------------------------------------

    def render_import(request: Request, status: int = 200, **extra: Any) -> HTMLResponse:
        snapshot = store.load_all()
        extra.setdefault("error", None)
        extra.setdefault("rows", None)
        extra.setdefault("review", None)
        return web.render(
            request, "pages/courses_import.html", IMPORT_PAGE, snapshot, status_code=status, **extra
        )

    @router.get("/courses/import", response_class=HTMLResponse)
    async def import_form(request: Request) -> HTMLResponse:
        return render_import(request)

    def call(text: str) -> tuple[dict | None, str | None]:
        try:
            result = run_skill(
                "extract-courses",
                importer.payload(text),
                load_schema("extract_courses"),
                config=config,
                action="import_courses",
                subject={},
                source="app",
            )
        except SkillError as err:
            return None, err.message
        return result.data, None

    def render_review(
        request: Request, text: str, original: str, sha: str, status: int = 200, error: str | None = None
    ) -> HTMLResponse:
        """The text that "Send to Claude" would send, each removed value highlighted; nothing sent yet."""
        return render_import(
            request,
            status,
            review=True,
            text=text,
            original_name=original,
            original_sha256=sha,
            marker=cfg.removed_marker,
            removed_count=text.count(cfg.removed_marker),
            error=error,
        )

    @router.post("/courses/import", response_class=HTMLResponse)
    async def import_read(request: Request) -> HTMLResponse:
        """Read courses: extract and strip, then the review step. No AI call happens here (DECISIONS #70)."""
        form = await request.form()
        upload = form.get("file")
        pasted = str(form.get("pasted", "") or "")
        try:
            if isinstance(upload, UploadFile) and upload.filename:
                data = await upload.read(cfg.max_upload_bytes + 1)
                text = extract.extract_text(upload.filename, data, cfg)
                original, sha = upload.filename, importer.sha256(data)
            elif pasted.strip():
                text = extract.text_from_paste(pasted)
                original, sha = PASTED, importer.sha256(pasted.encode("utf-8"))
            else:
                return render_import(request, 409, error=NOTHING_GIVEN)
            stripped = identifiers.strip(text, cfg)
        except (extract.ExtractError, identifiers.UnresolvedIdentifier) as err:
            return render_import(request, 409, error=str(err))
        return render_review(request, stripped, identifiers.clean_filename(original, cfg), sha)

    @router.post("/courses/import/send", response_class=HTMLResponse)
    async def import_send(request: Request) -> HTMLResponse:
        """Send to Claude: the reviewed text, stripped again (strict; stripping stripped text changes
        nothing), is the only document text sent. A failure shows the review again, so Send can be retried."""
        form = await request.form()
        original = identifiers.clean_filename(str(form.get("original_name", "")), cfg)
        sha = str(form.get("original_sha256", ""))
        sha = sha if re.fullmatch(r"[0-9a-f]{64}", sha) else "not recorded"
        try:
            stripped = identifiers.strip(_lf(str(form.get("text", ""))), cfg, marker_fills_slot=True)
        except identifiers.UnresolvedIdentifier as err:
            return render_import(request, 409, error=str(err))
        if not stripped.strip():
            return render_import(request, 409, error=NOTHING_GIVEN)
        output, error = await run_in_threadpool(call, stripped)
        if output is None:
            return render_review(request, stripped, original, sha, 409, error)
        rows = [importer.strip_row(r, cfg) for r in output["courses"]]
        return render_preview(request, stripped, original, sha, "", rows, output.get("warnings", []))

    def rows_from_form(form: Any) -> list[dict[str, Any]]:
        rows: dict[int, dict[str, Any]] = {}
        for key, value in form.multi_items():
            parts = key.split("-", 2)
            if len(parts) == 3 and parts[0] == "row" and parts[1].isdigit() and parts[2] in ROW_TEXT:
                rows.setdefault(int(parts[1]), {})[parts[2]] = str(value)
        out = []
        for i in sorted(rows):
            row = rows[i]
            credits = (row.get("credits") or "").strip().replace(",", ".")
            try:
                credit_value = float(credits) if credits else None
            except ValueError:
                credit_value = None
            out.append(
                {
                    "name": row.get("name", "").strip(),
                    "code": row.get("code", "").strip() or None,
                    "institution": row.get("institution", "").strip() or None,
                    "grade": row.get("grade", "").strip() or None,
                    "credits": credit_value,
                    "credits_text": row.get("credits", ""),
                    "term": row.get("term", "").strip() or None,
                    "lecturers": [n.strip() for n in _lf(row.get("lecturers", "")).split("\n") if n.strip()],
                    "description": _lf(row.get("description", "")),
                }
            )
        return out

    def checked_rows(rows: list[dict[str, Any]], text: str, source: str, snapshot: Snapshot) -> list[dict]:
        """Each row with its problems and whether it is already saved from this document."""
        mine = [c for c in snapshot.courses if source and c.source_document == source]
        others = [c for c in snapshot.courses if not (source and c.source_document == source)]
        out: list[dict] = []
        for n, row in enumerate(rows):
            saved = importer.is_duplicate(row, mine)
            problems = [] if saved else importer.row_problems(row, text, others, cfg)
            twin = next((j for j in range(n) if importer.same_course(rows[j], row)), None)
            if twin is not None and not saved:
                problems.append(f"Same course as row {twin + 1}")  # the document lists it twice
            out.append({**row, "saved": saved, "problems": problems})
        return out

    def render_preview(
        request: Request,
        text: str,
        original: str,
        sha: str,
        source: str,
        rows: list[dict[str, Any]],
        warnings: list[str],
        status: int = 200,
        error: str | None = None,
        notice: str | None = None,
        focus_row: int | None = None,
    ) -> HTMLResponse:
        snapshot = store.load_all()
        checked = checked_rows(rows, text, source, snapshot)
        return render_import(
            request,
            status,
            rows=checked,
            text=text,
            original_name=original,
            original_sha256=sha,
            source=source,
            warnings=warnings,
            error=error,
            notice=notice,
            any_saved=any(r["saved"] for r in checked),
            acceptable=sum(1 for r in checked if not r["saved"] and not r["problems"]),
            focus_row=focus_row,
        )

    @router.post("/courses/import/save", response_class=HTMLResponse)
    async def import_save(request: Request) -> HTMLResponse:
        form = await request.form()
        # checked again, never trusted; a browser posts line breaks as CRLF, stored as LF (#39(8))
        text = identifiers.strip(_lf(str(form.get("text", ""))), cfg, strict=False)
        original = identifiers.clean_filename(str(form.get("original_name", "")), cfg)
        sha = str(form.get("original_sha256", ""))
        sha = sha if re.fullmatch(r"[0-9a-f]{64}", sha) else "not recorded"
        source = str(form.get("source", ""))
        rows = [importer.strip_row(r, cfg) for r in rows_from_form(form)]
        accept = str(form.get("accept", ""))
        snapshot = store.load_all()
        if accept == "check":  # "Check again" after a correction: the checks, nothing written
            again = checked_rows(rows, text, source, snapshot)
            first = next((n for n, r in enumerate(again) if r["problems"]), None)  # stay where the work is
            if first is None:  # nothing flagged: the first course not yet saved (usually the one corrected)
                first = next((n for n, r in enumerate(again) if not r["saved"]), None)
            notice = "Checked again: every course can be accepted." if first is None else None
            return render_preview(
                request, text, original, sha, source, rows, [], notice=notice, focus_row=first
            )
        checked = checked_rows(rows, text, source, snapshot)
        if accept == "all":
            chosen = [i for i, r in enumerate(checked) if not r["saved"] and not r["problems"]]
        elif accept.isdigit() and int(accept) < len(checked):
            chosen = [int(accept)]
        else:
            chosen = []
        blocked = [checked[i] for i in chosen if checked[i]["saved"] or checked[i]["problems"]]
        if not chosen or blocked:
            reason = (
                "; ".join(blocked[0]["problems"])
                if blocked and blocked[0]["problems"]
                else "nothing to accept"
            )
            return render_preview(
                request,
                text,
                original,
                sha,
                source,
                rows,
                [],
                status=409,
                error=f"Nothing was saved: {reason}.",
            )
        try:
            store.load_courses()  # an unreadable courses.yaml stops here, before the source file is written
            if not source and sha != "not recorded":
                source = store.source_with_sha(sha) or ""  # accepted before from an older copy of the page
            if not source:
                today = web.today(snapshot).isoformat()
                source = importer.source_name(
                    original if original != PASTED else "", today, store.source_names()
                )
                store.create_source_text(source, importer.source_header(original, sha) + text)
            taken = {c.id for c in snapshot.courses}
            new = [
                importer.to_course(checked[i], importer.course_id(checked[i], taken), source) for i in chosen
            ]
            store.add_courses(new)
        except DataError as err:
            return render_preview(
                request,
                text,
                original,
                sha,
                source,
                rows,
                [],
                status=409,
                error=f"Nothing was saved: {err.path} {err.message}",
            )
        notice = f"Saved {len(new)} course{'s' if len(new) != 1 else ''}."
        focus = chosen[0] if accept != "all" else None  # Accept on one row: stay at that course
        return render_preview(request, text, original, sha, source, rows, [], notice=notice, focus_row=focus)

    # ---- professors from the document (US-20) ------------------------------------------------------

    def lecturer_groups(snapshot: Snapshot, source: str) -> list[lecturers.Lecturer]:
        courses = [c for c in snapshot.courses if c.source_document == source and c.confirmed]
        groups = lecturers.group(courses, config.names, cfg)
        lecturers.match_professors(groups, snapshot.professors, config.names)
        return groups

    def render_professors(
        request: Request, source: str, status: int = 200, error: str | None = None
    ) -> HTMLResponse:
        snapshot = store.load_all()
        return web.render(
            request,
            "pages/courses_professors.html",
            PROFESSORS_PAGE,
            snapshot,
            status_code=status,
            source=source,
            groups=lecturer_groups(snapshot, source),
            error=error,
        )

    @router.post("/courses/import/professors", response_class=HTMLResponse)
    async def professors(request: Request) -> Response:
        form = await request.form()
        source = str(form.get("source", ""))
        snapshot = store.load_all()
        groups = {g.key: g for g in lecturer_groups(snapshot, source)}
        if not groups:
            return render_professors(
                request, source, 409, "No confirmed course from this document has a lecturer."
            )
        if form.get("step") != "create":
            return render_professors(request, source)
        picks = [str(p) for p in form.getlist("pick")]
        creates, links, problems = [], [], []
        for pick in picks:
            kind, _, rest = pick.partition(":")
            key, _, pid = rest.partition(":")
            group = groups.get(key)
            if group is None:
                problems.append("a ticked lecturer is no longer in this document")
            elif kind == "create" and group.matches:
                problems.append(
                    f"{group.written} matches {group.matches[0][0].name}; link instead of creating"
                )
            elif kind == "create":
                creates.append(group)
            elif kind == "link" and any(p.id == pid for p, _ in group.matches):
                links.append((group, pid))
            else:
                problems.append("a ticked choice is not valid")
        if problems or not (creates or links):
            message = problems[0] if problems else "Tick at least one lecturer"
            return render_professors(request, source, 409, f"Nothing was saved: {message}.")
        folder = store.data_dir / "professors"
        taken = {p.id for p in snapshot.professors}
        if folder.is_dir():
            taken |= {f.stem for f in folder.glob("*.yaml")}
        new = [
            Professor(
                id=lecturers.professor_id(g.parsed.surname, taken),
                name=g.written,
                surname=g.parsed.surname,
                title=g.parsed.title,
                university=next((c.institution for c, _ in g.courses if c.institution), None) or "Other",
                course_ids=g.course_ids,
            )
            for g in creates
        ]
        done = 0
        for professor in new:
            try:
                store.create_professor(professor)
            except DataError as err:
                return render_professors(
                    request,
                    source,
                    409,
                    f"{done} of {len(new)} professors were created, then it stopped: "
                    f"{err.path} {err.message}.",
                )
            done += 1
        for group, pid in links:
            professor = next(p for p in store.load_professors()[0] if p.id == pid)
            missing = [i for i in group.course_ids if i not in professor.course_ids]
            if missing:
                professor.course_ids = [*professor.course_ids, *missing]
                try:
                    store.save_professor(professor)
                except DataError as err:
                    return render_professors(
                        request, source, 409, f"{professor.name}: {err.path} {err.message}."
                    )
        return see_other(f"/recommenders?created={done}&linked={len(links)}")

    # ---- Add professor suggestions (US-21) --------------------------------------------------------

    @router.get("/recommenders/course-suggestions", response_class=HTMLResponse)
    async def course_suggestions(request: Request, name: str = "") -> HTMLResponse:
        snapshot = store.load_all()
        parsed = names.parse(name, config.names) if name.strip() else None
        found = []
        if parsed and parsed.surname:
            for course in snapshot.courses:
                for written in course.lecturers:
                    if names.same_surname(names.parse(written, config.names).surname, parsed.surname):
                        found.append((course, written))
                        break
        ctx = web.context(IMPORT_PAGE, snapshot, suggestions=found, parsed=parsed, query=name)
        return web.templates.TemplateResponse(request, "partials/course_suggestions.html", ctx)

    return router
