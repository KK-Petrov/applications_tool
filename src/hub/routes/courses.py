"""Courses and course import (US-19 to US-21; docs/03 §1, §4.8; docs/04 §9; DECISIONS #44, #64).

- ``GET /courses``: the minimal list (the biography bank shows courses in full in Phase 4a), each with Edit.
- ``GET/POST /courses/{id}/edit``, ``POST /courses/{id}/delete``, ``GET /courses/deleted``,
  ``POST /courses/restore``: the owner corrects, deletes (refused while a professor links the course, or
  "unlink and delete") and restores courses; no AI call (DECISIONS #94).
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

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from hub.ai.skills import SkillError, load_schema, run_skill
from hub.courses import editing, extract, identifiers, importer, lecturers, merging
from hub.models.professor import Professor
from hub.rules import names
from hub.store import DataError, Snapshot
from hub.web import Page, Web, see_other

COURSES_PAGE = Page("recommenders", "/courses", "Recommenders", "Courses")
IMPORT_PAGE = Page("recommenders", "/courses/import", "Recommenders", "Import courses")
DELETED_PAGE = Page("recommenders", "/courses/deleted", "Recommenders", "Deleted courses")
MERGE_PAGE = Page("recommenders", "/courses/merge", "Recommenders", "Merge duplicate courses")
MERGE_PAGE_STALE = (
    "Your courses or professors changed since this page was shown (for example in another tab). "
    "Nothing was merged; your choices are shown again."
)
PROFESSORS_PAGE = Page("recommenders", "/courses/import/professors", "Recommenders", "Create professors")
FROM_COURSES_PAGE = Page(
    "recommenders", "/recommenders/from-courses", "Recommenders", "Professors from your courses"
)
ROW_TEXT = ("name", "code", "institution", "grade", "credits", "term", "lecturers", "description", "merged")
NOTHING_GIVEN = "Choose a file or paste the text of your transcript. Nothing was read."
# the Save step found the hidden text no longer strips: it was changed after Send (DECISIONS #82, #83)
SAVE_TEXT_CHANGED = "Nothing was saved: the text changed after it was checked. Read the document again."
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

    def owner() -> str:
        """The owner's name from settings: a line naming them loses its ID-shaped tokens (DECISIONS #93)."""
        return store.load_settings().owner_name

    # ---- the list --------------------------------------------------------------------------------

    def merge_all_notice(done: int, waiting: int) -> str:
        text = f"Merged {done} group{'' if done == 1 else 's'} of duplicates."
        if waiting:
            text += (
                f" {waiting} group{' needs' if waiting == 1 else 's need'} your choice on Merge duplicates."
            )
        return text

    @router.get("/courses", response_class=HTMLResponse)
    async def courses_list(request: Request) -> HTMLResponse:
        snapshot = store.load_all()
        q = request.query_params
        by_id = {c.id: c for c in snapshot.courses}
        notice = None
        if q.get("saved") in by_id:
            transcript = [f for f in q.get("transcript", "").split(",") if f in editing.FROM_TRANSCRIPT]
            notice = f"Saved {by_id[q['saved']].name}." + editing.transcript_warning(transcript)
        elif q.get("unchanged") in by_id:
            notice = f"Nothing changed in {by_id[q['unchanged']].name}; nothing was written."
        elif q.get("deleted"):
            notice = "Course deleted. You can bring it back from Deleted courses."
        elif q.get("merged") in by_id and q.get("count", "").isdigit():
            n = int(q["count"])
            notice = (
                f"Merged {n + 1} copies into {by_id[q['merged']].name}; "
                f"the other cop{'y is' if n == 1 else 'ies are'} in Deleted courses."
            )
        elif q.get("merged_all", "").isdigit() and q.get("waiting", "").isdigit():
            notice = merge_all_notice(int(q["merged_all"]), int(q["waiting"]))
        elif q.get("deleted_count", "").isdigit():
            n = int(q["deleted_count"])
            notice = (
                f"{n} course{'' if n == 1 else 's'} deleted. You can bring them back from Deleted courses."
            )
        elif q.get("restored", "").isdigit():
            notice = f"Restored {q['restored']} course{'' if q['restored'] == '1' else 's'}."
        return web.render(
            request,
            "pages/courses.html",
            COURSES_PAGE,
            snapshot,
            courses=snapshot.courses,
            notice=notice,
            error=None,
        )

    # ---- edit, delete, restore (DECISIONS #94) -------------------------------------------------------------

    def active(snapshot: Snapshot, course_id: str) -> Any:
        course = next((c for c in snapshot.courses if c.id == course_id), None)
        if course is None:
            raise HTTPException(status_code=404)
        return course

    def render_edit(
        request: Request,
        snapshot: Snapshot,
        course: Any,
        values: dict[str, str],
        errors: list,
        status: int = 200,
    ) -> HTMLResponse:
        page = Page("recommenders", f"/courses/{course.id}/edit", "Recommenders", f"Edit {course.name}")
        return web.render(
            request,
            "pages/course_edit.html",
            page,
            snapshot,
            status_code=status,
            course=course,
            values=values,
            errors=errors,
            labels=editing.LABELS,
        )

    @router.get("/courses/deleted", response_class=HTMLResponse)
    async def courses_deleted(request: Request) -> HTMLResponse:
        snapshot = store.load_all()
        every = store.load_courses().courses
        names = {c.id: c.name for c in every}
        deleted = [c for c in every if c.deleted]
        return web.render(
            request,
            "pages/courses_deleted.html",
            DELETED_PAGE,
            snapshot,
            deleted=deleted,
            error=None,
            names=names,
        )

    @router.post("/courses/restore", response_class=HTMLResponse)
    async def courses_restore(request: Request) -> Response:
        form = await request.form()
        wanted = {str(v) for v in form.getlist("restore")}
        snapshot = store.load_all()
        current = store.load_courses()
        deleted = [c for c in current.courses if c.deleted]
        chosen = [c for c in deleted if c.id in wanted]

        def refuse(message: str) -> HTMLResponse:
            return web.render(
                request,
                "pages/courses_deleted.html",
                DELETED_PAGE,
                snapshot,
                status_code=409,
                deleted=deleted,
                error=message,
                names={c.id: c.name for c in current.courses},
            )

        if not chosen:
            return refuse("Tick at least one course to bring back. Nothing was restored.")
        by_id = {c.id: c for c in current.courses}
        # two ticked courses that are the same course (a course and its merged copy) would come back as
        # duplicates: refused, naming both (data-integrity r11 F2)
        for i, course in enumerate(chosen):
            other = next((c for c in chosen[i + 1 :] if merging.same_course(c, course)), None)
            if other is not None:
                return refuse(
                    f"{course.name} and {other.name} are the same course: bring back only one. "
                    "Nothing was restored."
                )
        for course in chosen:  # an active course is the same course: restoring would duplicate it
            # the merge rule (code, or name, institutions equal or one empty), as "Merge into" at import
            # matches: an import copy differing in term or institution is still that course (ui r7 F2)
            twin = next((c for c in snapshot.courses if merging.same_course(c, course)), None)
            kept = merging.resolve(course.merged_into, by_id)
            if twin is not None and kept is not None and twin.id == kept.id:
                return refuse(
                    f"{course.name} was merged into {kept.name}, which is still on your list. "
                    "Nothing was restored."
                )
            if twin is not None:
                return refuse(
                    f"{course.name} is already among your courses (imported again). Nothing was restored."
                )
        # a course brought back is its own course again: no longer merged into another (ui r7 F2)
        back = {x.id for x in chosen}
        current.courses = [
            c.model_copy(update={"deleted": False, "merged_into": None}) if c.id in back else c
            for c in current.courses
        ]
        store.save_courses(current)
        return see_other(f"/courses?restored={len(chosen)}")

    # ---- merge duplicates (DECISIONS #95) ---------------------------------------------------------

    def link_counts(snapshot: Snapshot) -> dict[str, int]:
        counts: dict[str, int] = {}
        for professor in snapshot.professors:
            for cid in set(professor.course_ids):
                counts[cid] = counts.get(cid, 0) + 1
        return counts

    def source_of(course: Any) -> str:
        return store.source_text(course.source_document)

    def render_merge(
        request: Request,
        snapshot: Snapshot,
        status: int = 200,
        error: str | None = None,
        selected: list[Any] | None = None,
        posted: list[dict[str, Any]] | None = None,
        errors: dict[int, str] | None = None,
        notice: str | None = None,
    ) -> HTMLResponse:
        """The merge page, one form for every group (batch merge, DECISIONS #101): every group of duplicates,
        or (``selected``, from "Merge selected") one group of the ticked courses, or (``posted``) the groups
        that were posted, with the owner's ticks, kept copies and picks (ui review round 7, F1); ``errors``
        holds a message per group, shown inside it. The form carries a digest of courses.yaml and the
        professor files, so a submit after either changed is refused."""
        counts = link_counts(snapshot)
        if posted is not None:
            plans = [
                merging.plan(
                    g["copies"],
                    counts,
                    source_of,
                    g["keep"] if g["keep"] in {c.id for c in g["copies"]} else None,
                )
                for g in posted
            ]
        else:
            found = [selected] if selected else merging.groups(snapshot.courses)
            plans = [merging.plan(g, counts, source_of) for g in found]
        return web.render(
            request,
            "pages/courses_merge.html",
            MERGE_PAGE,
            snapshot,
            status_code=status,
            plans=plans,
            counts=counts,
            labels=editing.LABELS,
            error=error,
            errors=errors or {},
            notice=notice,
            posted=posted,
            digest=store.courses_and_professors_digest(),
        )

    def apply_merges(snapshot: Snapshot, merges: list[tuple[Any, set[str]]]) -> None:
        """Write merges: each (kept course, ids merged away). Every professor linking a merged-away copy is
        repointed to its kept course (no duplicates in the list), professors first, so a failure later leaves
        the copies in place; the copies get ``deleted: true`` and ``merged_into`` (ids never reused)."""
        into = {cid: kept.id for kept, away in merges for cid in away}
        for professor in snapshot.professors:
            if into.keys() & set(professor.course_ids):
                repointed: list[str] = []
                for cid in professor.course_ids:
                    target = into.get(cid, cid)
                    if target not in repointed:
                        repointed.append(target)
                store.save_professor(professor.model_copy(update={"course_ids": repointed}))
        kept_by_id = {kept.id: kept for kept, _ in merges}
        current = store.load_courses()
        current.courses = [
            kept_by_id[c.id]
            if c.id in kept_by_id
            else c.model_copy(update={"deleted": True, "merged_into": into[c.id]})
            if c.id in into
            else c
            for c in current.courses
        ]
        store.save_courses(current)

    @router.post("/courses/merge-all", response_class=HTMLResponse)
    async def merge_all(request: Request) -> Response:
        """Merge every group that needs no choice (no real conflict; a case-only difference takes the
        transcript's form; the default copy is kept). Groups with a real conflict are left for
        /courses/merge."""
        snapshot = store.load_all()
        counts = link_counts(snapshot)
        merges: list[tuple[Any, set[str]]] = []
        waiting = 0
        for found in merging.groups(snapshot.courses):
            plan = merging.plan(found, counts, source_of)
            if any(f.conflict and not f.preset for f in plan.fields):
                waiting += 1
                continue
            picks = {f.name: f.preset for f in plan.fields if f.conflict and f.preset}
            merges.append((merging.merged(plan, picks), {c.id for c in found if c.id != plan.keep}))
        if merges:
            apply_merges(snapshot, merges)
        return see_other(f"/courses?merged_all={len(merges)}&waiting={waiting}")

    @router.post("/courses/selected", response_class=HTMLResponse)
    async def courses_selected(request: Request) -> Response:
        """The list's selection: "Delete selected" (refused while professors link any, unless ``unlink=1``)
        or "Merge selected" (the merge preview of exactly the ticked courses; nothing written)."""
        form = await request.form()
        snapshot = store.load_all()
        wanted = [str(v) for v in form.getlist("select")]
        chosen = [c for c in snapshot.courses if c.id in wanted]
        action = str(form.get("action", ""))

        def refuse(message: str) -> HTMLResponse:
            return web.render(
                request,
                "pages/courses.html",
                COURSES_PAGE,
                snapshot,
                status_code=409,
                courses=snapshot.courses,
                notice=None,
                error=message,
            )

        if action == "merge":
            if len(chosen) < 2:
                return refuse("Select at least two courses to merge. Nothing was merged.")
            return render_merge(request, snapshot, selected=chosen)
        if action != "delete" or not chosen:
            return refuse(
                "Select at least one course, then Delete selected or Merge selected. Nothing was changed."
            )
        ids = {c.id for c in chosen}
        linking = [p for p in snapshot.professors if ids & set(p.course_ids)]
        if linking and form.get("unlink") != "1":
            page = Page("recommenders", "/courses/selected", "Recommenders", "Delete selected courses")
            return web.render(
                request,
                "pages/courses_delete_selected.html",
                page,
                snapshot,
                status_code=409,
                chosen=chosen,
                linking=linking,
            )
        for professor in linking:  # professors first: a failure later leaves the courses in place, unlinked
            store.save_professor(
                professor.model_copy(update={"course_ids": [i for i in professor.course_ids if i not in ids]})
            )
        current = store.load_courses()
        current.courses = [
            c.model_copy(update={"deleted": True}) if c.id in ids else c for c in current.courses
        ]
        store.save_courses(current)
        return see_other(f"/courses?deleted_count={len(ids)}")

    @router.get("/courses/merge", response_class=HTMLResponse)
    async def merge_form(request: Request) -> HTMLResponse:
        """Every group of duplicate courses with what merging them gives; nothing is written."""
        return render_merge(request, store.load_all())

    def posted_groups(form: Any, by_id: dict[str, Any]) -> list[dict[str, Any]] | None:
        """The groups of the merge form (``g<n>-ids``, ``-include``, ``-keep`` and a pick per field), or None
        when a course is gone or stands in two groups (the page is out of date)."""
        indexes = sorted(
            {int(m.group(1)) for key in form if (m := re.fullmatch(r"g(\d{1,3})-ids", str(key)))}
        )
        groups: list[dict[str, Any]] = []
        seen: set[str] = set()
        for n in indexes:
            ids = [str(v) for v in form.getlist(f"g{n}-ids")]
            if not ids or any(i not in by_id for i in ids) or seen & set(ids):
                return None
            seen |= set(ids)
            groups.append(
                {
                    "copies": [by_id[i] for i in ids],
                    "include": {str(v) for v in form.getlist(f"g{n}-include")},
                    "keep": str(form.get(f"g{n}-keep", "")),
                    "picks": {f: str(form.get(f"g{n}-{f}")) for f in editing.FIELDS if form.get(f"g{n}-{f}")},
                }
            )
        return groups or None

    def decide(snapshot: Snapshot, group: dict[str, Any]) -> tuple[tuple[Any, set[str]] | None, str | None]:
        """A group is decided when at least two copies are ticked, a ticked copy is kept and every real
        conflict is picked: then (kept course with the merged values, ids merged away), else the message."""
        copies = [c for c in group["copies"] if c.id in group["include"]]
        if len(copies) < 2:
            return None, "Tick at least two copies to merge. Nothing was merged."
        keep = group["keep"]
        if keep not in {c.id for c in copies}:
            return None, "Choose a ticked copy to keep. Nothing was merged."
        plan = merging.plan(copies, link_counts(snapshot), source_of, keep)
        try:
            kept = merging.merged(plan, group["picks"])
        except merging.MergeRefused as err:
            return None, str(err)
        return (kept, {c.id for c in copies if c.id != keep}), None

    def batch_notice(done: int, waiting: int) -> str:
        return (
            f"Merged {done} group{'' if done == 1 else 's'}; "
            f"{waiting} still need{'s' if waiting == 1 else ''} a choice."
        )

    @router.post("/courses/merge", response_class=HTMLResponse)
    async def merge_save(request: Request) -> Response:
        """A group's own button (``merge=<n>``) merges that group; "Merge all decided groups"
        (``merge=decided``) merges every decided group and shows the others again with the owner's choices
        (a group is all or nothing; DECISIONS #101). Merging: the kept course takes the merged values, every
        professor linking a merged-away copy is repointed to it (each professor written once), and the other
        copies go to Deleted courses with ``merged_into`` (their ids are never reused). The whole submit is
        refused, nothing written, when courses.yaml or a professor file changed since the page was shown."""
        form = await request.form()
        snapshot = store.load_all()
        groups = posted_groups(form, {c.id: c for c in snapshot.courses})
        if groups is None:
            return render_merge(
                request, snapshot, 409, "These courses changed since the page was shown. Nothing was merged."
            )
        if form.get("digest") != store.courses_and_professors_digest():
            return render_merge(request, snapshot, 409, MERGE_PAGE_STALE, posted=groups)
        action = str(form.get("merge", ""))
        if action.isdigit() and int(action) < len(groups):  # one group's own button
            n = int(action)
            result, message = decide(snapshot, groups[n])
            if result is None:
                return render_merge(request, snapshot, 409, posted=groups, errors={n: message or ""})
            apply_merges(snapshot, [result])
            kept, away = result
            return see_other(f"/courses?merged={kept.id}&count={len(away)}")
        if action != "decided":
            return render_merge(
                request, snapshot, 409, "Choose a Merge button. Nothing was merged.", posted=groups
            )
        decisions = [decide(snapshot, g) for g in groups]
        merges = [result for result, _ in decisions if result is not None]
        waiting = [
            (g, message or "")
            for g, (result, message) in zip(groups, decisions, strict=True)
            if result is None
        ]
        if merges:
            apply_merges(snapshot, merges)  # one write per professor for every group together
        if not waiting:
            return see_other(f"/courses?merged_all={len(merges)}&waiting=0")
        fresh = store.load_all()  # the undecided groups' courses are unchanged; shown with the posted choices
        by_id = {c.id: c for c in fresh.courses}
        left = [dict(g, copies=[by_id[c.id] for c in g["copies"]]) for g, _ in waiting]
        return render_merge(
            request,
            fresh,
            200 if merges else 409,
            posted=left,
            errors={n: message for n, (_, message) in enumerate(waiting)},
            notice=batch_notice(len(merges), len(waiting)),
        )

    @router.get("/courses/{course_id}/edit", response_class=HTMLResponse)
    async def course_edit_form(request: Request, course_id: str) -> HTMLResponse:
        snapshot = store.load_all()
        course = active(snapshot, course_id)
        return render_edit(request, snapshot, course, editing.form_values(course), [])

    @router.post("/courses/{course_id}/edit", response_class=HTMLResponse)
    async def course_edit(request: Request, course_id: str) -> Response:
        """Save the owner's corrections: changed fields join ``edited``; an unchanged save writes nothing; no
        AI call (DECISIONS #94)."""
        form = await request.form()
        snapshot = store.load_all()
        course = active(snapshot, course_id)
        outcome = editing.apply(course, form, cfg, owner())
        if outcome.course is None:
            return render_edit(request, snapshot, course, outcome.values, outcome.errors, status=409)
        if not outcome.changed:
            return see_other(f"/courses?unchanged={course.id}")
        current = store.load_courses()
        current.courses = [outcome.course if c.id == course.id else c for c in current.courses]
        store.save_courses(current)
        transcript = ",".join(f for f in outcome.changed if f in editing.FROM_TRANSCRIPT)
        return see_other(f"/courses?saved={course.id}" + (f"&transcript={transcript}" if transcript else ""))

    @router.post("/courses/{course_id}/delete", response_class=HTMLResponse)
    async def course_delete(request: Request, course_id: str) -> Response:
        """Delete (kept with ``deleted: true``, hidden everywhere, restorable). Refused while a professor
        links the course; ``unlink=1`` ("Unlink from these professors and delete") unlinks them first."""
        form = await request.form()
        snapshot = store.load_all()
        course = active(snapshot, course_id)
        linking = [p for p in snapshot.professors if course.id in p.course_ids]
        if linking and form.get("unlink") != "1":
            page = Page(
                "recommenders", f"/courses/{course.id}/delete", "Recommenders", f"Delete {course.name}"
            )
            return web.render(
                request,
                "pages/course_delete.html",
                page,
                snapshot,
                status_code=409,
                course=course,
                linking=linking,
            )
        for professor in linking:  # professors first: a failure later leaves the course in place, unlinked
            store.save_professor(
                professor.model_copy(
                    update={"course_ids": [i for i in professor.course_ids if i != course.id]}
                )
            )
        current = store.load_courses()
        current.courses = [
            c.model_copy(update={"deleted": True}) if c.id == course.id else c for c in current.courses
        ]
        store.save_courses(current)
        return see_other(f"/courses?deleted={course.id}")

    # ---- import ----------------------------------------------------------------------------------

    def render_import(request: Request, status: int = 200, **extra: Any) -> HTMLResponse:
        snapshot = store.load_all()
        extra.setdefault("error", None)
        extra.setdefault("rows", None)
        extra.setdefault("review", None)
        extra.setdefault("focus_notice", False)
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
            stripped = identifiers.strip(text, cfg, owner=owner())
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
            stripped = identifiers.strip(
                _lf(str(form.get("text", ""))), cfg, marker_fills_slot=True, owner=owner()
            )
        except identifiers.UnresolvedIdentifier as err:
            return render_import(request, 409, error=str(err))
        if not stripped.strip():
            return render_import(request, 409, error=NOTHING_GIVEN)
        output, error = await run_in_threadpool(call, stripped)
        if output is None:
            return render_review(request, stripped, original, sha, 409, error)
        rows = [importer.strip_row(r, cfg, owner()) for r in output["courses"]]
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
                    "merged": row.get("merged", "").strip() or None,  # merged into this stored course (#95)
                }
            )
        return out

    def checked_rows(rows: list[dict[str, Any]], text: str, source: str, snapshot: Snapshot) -> list[dict]:
        """Each row with its problems, whether it is already saved from this document, and the stored course
        it matches (offered "Merge into", DECISIONS #95: no "Already imported")."""
        mine = [c for c in snapshot.courses if source and c.source_document == source]
        others = [c for c in snapshot.courses if not (source and c.source_document == source)]
        by_id = {c.id: c for c in snapshot.courses}
        out: list[dict] = []
        for n, row in enumerate(rows):
            saved = importer.is_duplicate(row, mine) or row.get("merged") in by_id
            target = None if saved else next((c for c in others if merging.same_course(row, c)), None)
            problems = [] if saved else importer.row_problems(row, text, others, cfg, rows)
            merge: dict[str, Any] | None = None
            if target is not None:
                problems = [p for p in problems if p != importer.ALREADY_IMPORTED]
                _, differs = merging.fill_empty(target, row)
                merge = {
                    "id": target.id,
                    "name": target.name,
                    "differs": [(editing.LABELS[f], getattr(target, f), row.get(f)) for f in differs],
                }
            if row.get("merged") in by_id:
                merge = {"id": row["merged"], "name": by_id[row["merged"]].name, "differs": [], "done": True}
            twin = next((j for j in range(n) if importer.same_course(rows[j], row)), None)
            if twin is not None and not saved:
                problems.append(f"Same course as row {twin + 1}")  # the document lists it twice
            out.append(
                {
                    **row,
                    "saved": saved,
                    "merge": merge,
                    "problems": problems,
                    "problem_fields": problem_fields(problems),
                }
            )
        return out

    def problem_fields(problems: list[str]) -> dict[str, list[int]]:
        """For each field a problem is about, the indexes of those problems: the field is marked invalid and
        described by them (``aria-describedby``; ui review round 3, F1). A problem about the whole row (a
        duplicate) is tied only to the row's Accept button."""
        out: dict[str, list[int]] = {}
        for n, message in enumerate(problems):
            name = importer.problem_field(message)
            if name:
                out.setdefault(name, []).append(n)
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
        problem: dict[str, Any] | None = None,
        focus_notice: bool = False,
        owner_institution: str = "",
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
            acceptable=sum(1 for r in checked if not r["saved"] and not r["problems"] and not r["merge"]),
            focus_row=focus_row,
            problem=problem,
            focus_notice=focus_notice,
            owner_institution=owner_institution,
        )

    def problem_notice(checked: list[dict[str, Any]], first: int) -> dict[str, Any]:
        """What Check again tells the user when a course still has a problem (ui review round 3, F1;
        DECISIONS #75): how many courses, which one it lands on and why, shown at the top of the page with a
        link to that course (so it works without JS) and announced."""
        flagged = sum(1 for r in checked if r["problems"])
        row = checked[first]
        course = f"Course {first + 1}" + (f" ({row['name']})" if row.get("name") else "")
        reason = "; ".join(row["problems"])
        lead = (
            "1 course still has a problem:"
            if flagged == 1
            else f"{flagged} courses still have a problem. First:"
        )
        return {
            "lead": lead,
            "row": first,
            "course": course,
            "reason": reason,
            "text": f"{lead} {course}: {reason}.",
        }

    def source_file(source: str, original: str, sha: str, text: str, snapshot: Snapshot) -> str:
        """The document's sources/ file: the one named in the form, else one made earlier from the same
        document (sha256), else a new one with the stripped text (written now)."""
        if not source and sha != "not recorded":
            source = store.source_with_sha(sha) or ""  # accepted before from an older copy of the page
        if not source:
            today = web.today(snapshot).isoformat()
            source = importer.source_name(original if original != PASTED else "", today, store.source_names())
            store.create_source_text(source, importer.source_header(original, sha) + text)
        return source

    def merge_row(
        request: Request,
        i: int,
        checked: list[dict[str, Any]],
        rows: list[dict[str, Any]],
        text: str,
        original: str,
        sha: str,
        source: str,
        snapshot: Snapshot,
        owner_institution: str,
    ) -> HTMLResponse:
        """ "Merge into <course>" (DECISIONS #95): the stored course gets only its empty fields filled from
        this row (a transcript's values, so not owner-edited); where they differ the stored value is kept."""
        row = checked[i]
        merge = row["merge"]

        def refuse(reason: str) -> HTMLResponse:
            return render_preview(
                request, text, original, sha, source, rows, [], owner_institution=owner_institution,
                status=409, error=f"Nothing was saved: {reason}.",
            )  # fmt: skip

        if not merge or merge.get("done"):
            return refuse("this course matches none of your courses")
        if row["problems"]:
            return refuse("; ".join(row["problems"]))
        try:
            current = store.load_courses()
            target = next((c for c in current.courses if c.id == merge["id"] and not c.deleted), None)
            if target is None:
                return refuse("the course to merge into is gone")
            updated, _ = merging.fill_empty(target, row)
            filled = [f for f in editing.FIELDS if getattr(updated, f) != getattr(target, f)]
            source = source_file(source, original, sha, text, snapshot)
            courses = [updated if c.id == target.id else c for c in current.courses]
            if not merging.has_copy(current.courses, row, source, target.id):
                # the row kept as a retired copy of this document: its lecturers stay in the document's
                # Create professors step, linked to the course that remains (DECISIONS #96)
                taken = {c.id for c in current.courses} | set(snapshot.all_course_ids)
                copy = importer.to_course(row, importer.course_id(row, taken), source)
                courses.append(copy.model_copy(update={"deleted": True, "merged_into": target.id}))
            current.courses = courses
            store.save_courses(current)
        except DataError as err:
            return refuse(f"{err.path} {err.message}")
        rows = [dict(r, merged=target.id) if n == i else r for n, r in enumerate(rows)]
        notice = f"Merged into {target.name}: " + (
            f"filled {', '.join(editing.LABELS[f].lower() for f in filled)}."
            if filled
            else "nothing was empty to fill."
        )
        return render_preview(
            request, text, original, sha, source, rows, [], notice=notice, focus_row=i,
            owner_institution=owner_institution,
        )  # fmt: skip

    @router.post("/courses/import/save", response_class=HTMLResponse)
    async def import_save(request: Request) -> HTMLResponse:
        form = await request.form()
        # checked again, never trusted; a browser posts line breaks as CRLF, stored as LF (#39(8)).
        # Stripped as Send does (strict, the marker fills a slot): the text was stripped at Read and at
        # Send, so this changes nothing; a non-strict strip took the course code after "Student number:
        # [removed]" and wrote it so to sources/ (data-integrity review round 6, F3). A stop means the
        # hidden text was edited: refused, nothing written (DECISIONS #82)
        try:
            text = identifiers.strip(
                _lf(str(form.get("text", ""))), cfg, marker_fills_slot=True, owner=owner()
            )
        except identifiers.UnresolvedIdentifier:
            return render_import(request, 409, error=SAVE_TEXT_CHANGED)  # round 7, F7
        original = identifiers.clean_filename(str(form.get("original_name", "")), cfg)
        sha = str(form.get("original_sha256", ""))
        sha = sha if re.fullmatch(r"[0-9a-f]{64}", sha) else "not recorded"
        source = str(form.get("source", ""))
        name = owner()
        # the owner's own institution entry, for every course whose institution is empty (#93): typed by the
        # owner after Send, so never sent to Claude; stripped like any field
        owner_institution = identifiers.strip(
            " ".join(str(form.get("owner_institution", "")).split()), cfg, strict=False, owner=name
        )
        rows = [
            importer.with_owner_institution(importer.strip_row(r, cfg, name), owner_institution)
            for r in rows_from_form(form)
        ]
        accept = str(form.get("accept", ""))
        snapshot = store.load_all()
        if accept == "check":  # "Check again" after a correction: the checks, nothing written
            again = checked_rows(rows, text, source, snapshot)
            flagged = next((n for n, r in enumerate(again) if r["problems"]), None)  # stay where the work is
            problem = problem_notice(again, flagged) if flagged is not None else None
            first, notice = flagged, None
            if flagged is None:  # nothing flagged: the first course not yet saved (usually the one corrected)
                first = next((n for n, r in enumerate(again) if not r["saved"]), None)
                notice = (  # ui review round 4, N1: never a problem notice without a problem
                    "Checked again: every course can be accepted."
                    if first is not None
                    else "Checked again: every course is saved."
                )
                first = None  # the notice gets focus, shown above the first course (ui round 5, W1; #85)
            return render_preview(
                request,
                text,
                original,
                sha,
                source,
                rows,
                [],
                owner_institution=owner_institution,
                notice=notice,
                focus_row=first,
                problem=problem,
                focus_notice=flagged is None and notice.endswith("can be accepted."),
            )
        checked = checked_rows(rows, text, source, snapshot)
        if accept.startswith("merge-") and accept[6:].isdigit() and int(accept[6:]) < len(checked):
            return merge_row(
                request,
                int(accept[6:]),
                checked,
                rows,
                text,
                original,
                sha,
                source,
                snapshot,
                owner_institution,
            )
        if accept == "all":  # rows matching a stored course are merged one by one, never by Accept all (#95)
            chosen = [
                i for i, r in enumerate(checked) if not r["saved"] and not r["problems"] and not r["merge"]
            ]
        elif accept.isdigit() and int(accept) < len(checked):
            chosen = [int(accept)]
        else:
            chosen = []
        blocked = [
            checked[i] for i in chosen if checked[i]["saved"] or checked[i]["problems"] or checked[i]["merge"]
        ]
        if not chosen or blocked:
            reason = (
                "; ".join(blocked[0]["problems"])
                if blocked and blocked[0]["problems"]
                else f"it matches your course {blocked[0]['merge']['name']}; use Merge into"
                if blocked and blocked[0]["merge"]
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
                owner_institution=owner_institution,
                status=409,
                error=f"Nothing was saved: {reason}.",
            )
        try:
            store.load_courses()  # an unreadable courses.yaml stops here, before the source file is written
            source = source_file(source, original, sha, text, snapshot)
            taken = set(snapshot.all_course_ids)  # deleted courses' ids too: never reused (#94)
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
                owner_institution=owner_institution,
                status=409,
                error=f"Nothing was saved: {err.path} {err.message}",
            )
        notice = f"Saved {len(new)} course{'s' if len(new) != 1 else ''}."
        focus = chosen[0] if accept != "all" else None  # Accept on one row: stay at that course
        return render_preview(
            request,
            text,
            original,
            sha,
            source,
            rows,
            [],
            notice=notice,
            focus_row=focus,
            owner_institution=owner_institution,
        )

    # ---- professors from the document (US-20) and from every course (DECISIONS #102) -----------------

    def matched(courses: list[Any], snapshot: Snapshot) -> list[lecturers.Lecturer]:
        groups = lecturers.group(courses, config.names, cfg)
        lecturers.match_professors(groups, snapshot.professors, config.names)
        return groups

    def lecturer_groups(snapshot: Snapshot, source: str) -> list[lecturers.Lecturer]:
        # its own courses and the courses its merged copies went into (DECISIONS #96); deleted ones are read
        return matched(merging.document_courses(store.load_courses().courses, source), snapshot)

    def all_course_groups(snapshot: Snapshot) -> list[lecturers.Lecturer]:
        """Every lecturer of the active courses once (#102): ``Snapshot.courses`` leaves deleted ones out."""
        return matched([c for c in snapshot.courses if c.lecturers], snapshot)

    def render_professors(
        request: Request,
        source: str,
        status: int = 200,
        error: str | None = None,
        ticked: set[str] | None = None,
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
            ticked=ticked or set(),
            action="/courses/import/professors",
            everything=False,
        )

    def render_from_courses(
        request: Request, status: int = 200, error: str | None = None, ticked: set[str] | None = None
    ) -> HTMLResponse:
        snapshot = store.load_all()
        return web.render(
            request,
            "pages/courses_professors.html",
            FROM_COURSES_PAGE,
            snapshot,
            status_code=status,
            source="",
            groups=all_course_groups(snapshot),
            error=error,
            ticked=ticked or set(),
            action=FROM_COURSES_PAGE.href,
            everything=True,
        )

    async def create_or_link(
        form: Any,
        groups: dict[str, lecturers.Lecturer],
        snapshot: Snapshot,
        render: Any,
        gone: str,
    ) -> Response:
        """The ticked choices (``pick``: ``create:<key>`` or ``link:<key>:<professor id>``) checked first,
        then the new professors created in one batch through ``Store.create_professor`` (ids chosen first,
        never over a file; a failure part way says how many were created) and the links added (US-20,
        #102)."""
        picks = [str(p) for p in form.getlist("pick")]
        creates, links, problems = [], [], []
        for pick in picks:
            kind, _, rest = pick.partition(":")
            key, _, pid = rest.partition(":")
            group = groups.get(key)
            if kind not in ("create", "link"):
                problems.append("a ticked choice is not valid")
            elif group is None:
                problems.append(f"a ticked lecturer is no longer {gone}")
            elif kind == "create" and group.matches:
                problems.append(
                    f"{group.written} matches {group.matches[0][0].name}; link instead of creating"
                )
            elif kind == "create":
                creates.append(group)
            elif any(p.id == pid for p, _ in group.matches):
                links.append((group, pid))
            else:
                problems.append("a ticked choice is not valid")
        if problems or not (creates or links):
            message = problems[0] if problems else "Tick at least one lecturer"
            return render(409, f"Nothing was saved: {message}.", set(picks))
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
                stopped = f"{done} of {len(new)} professors were created, then it stopped"
                return render(409, f"{stopped}: {err.path} {err.message}.", None)
            done += 1
        for group, pid in links:
            professor = next(p for p in store.load_professors()[0] if p.id == pid)
            missing = [i for i in group.course_ids if i not in professor.course_ids]
            if missing:
                professor.course_ids = [*professor.course_ids, *missing]
                try:
                    store.save_professor(professor)
                except DataError as err:
                    return render(409, f"{professor.name}: {err.path} {err.message}.", None)
        return see_other(f"/recommenders?created={done}&linked={len(links)}")

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
        return await create_or_link(
            form,
            groups,
            snapshot,
            lambda status, error, ticked: render_professors(request, source, status, error, ticked),
            "in this document",
        )

    @router.get("/recommenders/from-courses", response_class=HTMLResponse)
    async def from_courses(request: Request) -> HTMLResponse:
        """Professors from your courses (#102): every lecturer of the active courses once; nothing ticked,
        nothing written."""
        return render_from_courses(request)

    @router.post("/recommenders/from-courses", response_class=HTMLResponse)
    async def from_courses_save(request: Request) -> Response:
        """ "Tick all" / "Clear all" (``select``; the page again, nothing written) or Create
        (``step=create``)."""
        form = await request.form()
        snapshot = store.load_all()
        groups = all_course_groups(snapshot)
        select = str(form.get("select", ""))
        if select in ("all", "none"):
            ticked: set[str] = set()
            if select == "all":  # every Create and every exact-match Link; a "same surname only" link stays
                for g in groups:
                    if not g.matches:
                        ticked.add(f"create:{g.key}")
                    ticked |= {f"link:{g.key}:{p.id}" for p, kind in g.matches if kind == "exact"}
            return render_from_courses(request, ticked=ticked)
        if form.get("step") != "create":
            return render_from_courses(request, ticked={str(p) for p in form.getlist("pick")})
        return await create_or_link(
            form,
            {g.key: g for g in groups},
            snapshot,
            lambda status, error, ticked: render_from_courses(request, status, error, ticked),
            "among your courses",
        )

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
