"""The AI usage page, ``GET /usage`` (docs/03 §4.9; DECISIONS #60). Read-only: there is no write route.

It lists every logged AI call: when (display zone), which action, what it was for, how long it took, its
estimated cost and how it ended. Totals cover this month and all time, by action (following the source
filter) and by source (always every source). Names come from the current data: a professor or programme
still in data_dir is shown by name and linked; one that has been deleted shows its ID. Eval and live calls
use invented IDs and are never resolved or linked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from hub.ai import usage
from hub.rules.timezones import MONTHS_LONG
from hub.store import Snapshot
from hub.web import Page, Web

USAGE_PAGE = Page("usage", "/usage", "AI usage", "AI usage")
COST_NOTE = (
    "Estimated API-equivalent cost. On your subscription this draws from your monthly Agent SDK credit "
    "and isn't billed separately unless usage credits are enabled."
)
DEFAULT_MONTH = "all"
DEFAULT_SOURCE = "app"
SOURCE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("app", "App"),
    ("eval", "Eval"),
    ("live", "Live"),
    ("unknown", "Unknown (before the audit log)"),
    ("all", "All sources"),
)
MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])")  # used with fullmatch
# What a call without a subject was for (docs/04 §8): research and import never log their query or document.
EMPTY_SUBJECT = {
    "research_programme": "A new programme",
    "import_courses": "A course document",
    "live_check": "Skill and lockdown checks",
}
NOT_RECORDED = "Not recorded"


@dataclass(frozen=True)
class Part:
    """One piece of "what it was for": a name (linked when the item still exists) or an ID."""

    text: str
    href: str | None = None
    missing: bool = False  # the item is no longer in data_dir: its ID is shown with a note
    is_id: bool = False  # an eval/live ID (invented), shown as an ID, never linked
    prefix: str = ""  # plain words before the name, e.g. "CV for "


@dataclass(frozen=True)
class Row:
    line: usage.UsageLine
    action: str
    target: list[Part]  # the professor (if any), then its programmes
    others: list[Part]  # programmes, essay or CV when there is no professor
    duration: str
    cost: str
    result: str
    ok: bool


def month_label(key: str) -> str:
    """ "2026-10" → "October 2026"."""
    year, month = key.split("-")
    return f"{MONTHS_LONG[int(month) - 1]} {year}"


def cost_text(cost: float | None) -> str:
    """Three decimals everywhere, so totals add up as shown and a $0.004 call doesn't read as free."""
    return "not reported" if cost is None else f"${cost:.3f}"


def calls_text(n: int) -> str:
    return f"{n} call" if n == 1 else f"{n} calls"


def errors_text(n: int) -> str:
    return "none" if n == 0 else (f"{n} error" if n == 1 else f"{n} errors")


def _parts(line: usage.UsageLine, snapshot: Snapshot) -> tuple[list[Part], list[Part]]:
    """The professor part and the other parts of a row's "what it was for" column."""
    resolve = line.source in usage.APP_LOG_SOURCES  # eval/live IDs are invented: never resolved or linked
    professors = {p.id: p for p in snapshot.professors} if resolve else {}
    programmes = {p.id: p for p in snapshot.programmes} if resolve else {}

    def programme(pid: str) -> Part:
        if not resolve:
            return Part(pid, is_id=True)
        found = programmes.get(pid)
        return Part(found.short_name, f"/programmes/{pid}") if found else Part(pid, missing=True)

    subject = line.subject
    head: list[Part] = []
    rest: list[Part] = []
    if "professor_id" in subject:
        pid = str(subject["professor_id"])
        if not resolve:
            head.append(Part(pid, is_id=True))
        else:
            prof = professors.get(pid)
            head.append(Part(prof.name, f"/recommenders?selected={pid}") if prof else Part(pid, missing=True))
    ids = list(subject.get("programme_ids", []))
    if "programme_id" in subject:
        ids.insert(0, str(subject["programme_id"]))
    rest += [programme(x) for x in ids]
    if "essay_id" in subject:
        rest.append(Part(str(subject["essay_id"]), is_id=True, prefix="essay "))  # resolved from Phase 4a
    if "cv_id" in subject:
        cv = str(subject["cv_id"])
        if cv == "base":
            rest.append(Part("base CV"))
        else:
            found = programme(cv)
            rest.append(Part(found.text, found.href, found.missing, found.is_id, prefix="CV for "))
    if not head and not rest:
        rest.append(Part(EMPTY_SUBJECT.get(line.action, NOT_RECORDED)))
    return head, rest


def _row(line: usage.UsageLine, snapshot: Snapshot) -> Row:
    head, rest = _parts(line, snapshot)
    result = "Done" if line.ok else f"Error: {usage.ERROR_LABELS.get(line.error or '', 'unknown')}"
    return Row(
        line=line,
        action=usage.ACTIONS[line.action],
        target=head,
        others=rest,
        duration="not reported" if line.duration_s is None else f"{line.duration_s:.1f} s",
        cost=cost_text(line.cost_usd),
        result=result,
        ok=line.ok,
    )


def _totals_rows(pairs: dict[str, usage.Pair], labels: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {
            "label": labels[key],
            "month_calls": calls_text(p.month.calls),
            "month_cost": cost_text(p.month.cost_usd),
            "all_calls": calls_text(p.all_time.calls),
            "all_cost": cost_text(p.all_time.cost_usd),
            "errors": errors_text(p.all_time.errors),
        }
        for key, p in pairs.items()
    ]


def build_router(web: Web) -> APIRouter:
    router = APIRouter()
    config = web.config

    @router.get("/usage", response_class=HTMLResponse, name="usage")
    async def usage_page(request: Request) -> HTMLResponse:
        snapshot = web.store.load_all()
        tz = web.display_tz(snapshot)
        month = request.query_params.get("month") or DEFAULT_MONTH
        if month != DEFAULT_MONTH and not MONTH.fullmatch(month):
            month = DEFAULT_MONTH
        source = request.query_params.get("source") or DEFAULT_SOURCE
        if source not in dict(SOURCE_OPTIONS):
            source = DEFAULT_SOURCE

        log = usage.read_all(config)
        now = web.clock()
        chosen = log.lines if source == "all" else [x for x in log.lines if x.source == source]
        shown = (
            chosen if month == DEFAULT_MONTH else [x for x in chosen if usage.month_key(x.at, tz) == month]
        )
        everything = usage.totals(log.lines, now, tz)
        filtered = usage.totals(chosen, now, tz)
        months = sorted({usage.month_key(x.at, tz) for x in log.lines}, reverse=True)
        if month != DEFAULT_MONTH and month not in months:  # the select shows the month asked for
            months = sorted([*months, month], reverse=True)
        # By source lists every source, with zeros for one without calls (docs/03 §4.9)
        every_source = {k: everything.by_source.get(k, usage.Pair()) for k in usage.SOURCES}
        source_rows = _totals_rows(every_source, usage.SOURCES)
        source_rows.append(_totals_rows({"all": everything.overall}, {"all": "All sources"})[0])
        return web.render(
            request,
            "pages/usage.html",
            USAGE_PAGE,
            snapshot,
            has_lines=bool(log.lines),
            rows=[_row(x, snapshot) for x in shown],
            problems=log.problems,
            legacy_pending=log.legacy_pending,
            run_log_label=Path(config.claude.run_usage_log).as_posix(),
            month=month,
            source=source,
            default_filters=month == DEFAULT_MONTH and source == DEFAULT_SOURCE,
            whole_file_problem=any(p.line is None for p in log.problems),
            source_label=dict(SOURCE_OPTIONS)[source],
            month_options=[(m, month_label(m)) for m in months],
            source_options=SOURCE_OPTIONS,
            current_month_label=month_label(everything.month),
            by_action=_totals_rows(filtered.by_action, usage.ACTIONS),
            by_source=source_rows,
            no_cost=everything.overall.all_time.no_cost,
            cost_note=COST_NOTE,
        )

    return router
