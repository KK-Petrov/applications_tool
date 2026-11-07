"""The AI usage log: one line per AI call, saying what it was for, what it cost and how it ended.

docs/01 §11 and docs/04 §8 (DECISIONS #60). ``run_skill`` writes a line for every call, whatever happens,
from a fixed set of fields:

- ``action``: the feature that made the call (``generate_email``, ``research_programme`` …; see ``ACTIONS``).
- ``subject``: what the call was for, as **IDs only** (``professor_id``, ``programme_ids``, ``programme_id``,
  ``essay_id``, ``cv_id``). Every value must be an ID slug. That keeps names, email text, essay text,
  biography text and research queries out of the log, even by mistake.
- ``source``: ``app`` (a click in the app), ``eval`` (``tasks.py eval-*``) or ``live`` (live checks and
  re-recording). Lines imported from the old single log are ``unknown``.

The app's lines go to ``<data_dir>/logs/ai_usage.jsonl``, backed up with the rest of the data folder. Eval
and live lines go to the run log, ``reports/ai_usage.jsonl`` (gitignored). The AI usage page reads both.
Nothing here ever reads or stores a payload or an output.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    ValidationError,
)

from hub.config import Config
from hub.models.common import SLUG_PATTERN

log = logging.getLogger(__name__)

# The feature behind each call, in the order the page lists them. "unlabelled" only marks lines imported
# from the old reports/ai_usage.jsonl; run_skill refuses it.
ACTIONS: dict[str, str] = {
    "generate_email": "Generate email",
    "research_programme": "Research programme",
    "reresearch_programme": "Re-research programme",
    "import_courses": "Import courses",
    "evaluate_essay": "Evaluate essay",
    "evaluate_cv": "Evaluate CV",
    "grade_claims": "Grade claims (eval)",
    "live_check": "Live check",
    "unlabelled": "Unlabelled",
}
UNLABELLED = "unlabelled"
# Where a call came from. "unknown" is only for imported lines.
SOURCES: dict[str, str] = {
    "app": "App",
    "eval": "Eval",
    "live": "Live",
    "unknown": "Unknown (before the audit log)",
}
CALL_SOURCES = ("app", "eval", "live")
APP_LOG_SOURCES = frozenset({"app", "unknown"})
RUN_LOG_SOURCES = frozenset({"eval", "live"})
ERROR_LABELS: dict[str, str] = {
    "not_installed": "Claude Code not found",
    "timeout": "timed out",
    "failed": "Claude Code failed",
    "invalid_output": "unusable output",
}
SUBJECT_KEYS = ("professor_id", "programme_ids", "programme_id", "essay_id", "cv_id")
LIST_KEYS = frozenset({"programme_ids"})
FIELDS = (
    "at",
    "skill",
    "backend",
    "action",
    "subject",
    "source",
    "duration_s",
    "total_cost_usd",
    "ok",
    "error",
)
_ID = re.compile(SLUG_PATTERN)
_LOCK = threading.Lock()

Subject = dict[str, str | list[str]]


# ---- what a call is for ------------------------------------------------------------------------------


def validate_subject(subject: Any) -> Subject:
    """The subject in key order, if it is IDs only; otherwise ``ValueError``.

    Allowed keys: ``SUBJECT_KEYS``. ``programme_ids`` is a list of IDs, every other key a single ID.
    """
    if not isinstance(subject, Mapping):
        raise ValueError("subject must be a mapping of IDs")
    unknown = sorted(set(subject) - set(SUBJECT_KEYS))
    if unknown:
        raise ValueError(f"subject keys {unknown} are not allowed")
    out: Subject = {}
    for key in SUBJECT_KEYS:
        if key not in subject:
            continue
        value = subject[key]
        if key in LIST_KEYS:
            if not isinstance(value, list | tuple) or not all(_is_id(v) for v in value):
                raise ValueError(f"subject {key} must be a list of IDs")
            out[key] = list(value)
        else:
            if not _is_id(value):
                raise ValueError(f"subject {key} must be an ID")
            out[key] = value
    return out


def _is_id(value: Any) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None  # fullmatch: no trailing newline


def check_call(action: str, subject: Any, source: str) -> Subject:
    """Check what a ``run_skill`` call says about itself before it runs; ``ValueError`` if it is wrong."""
    if action not in ACTIONS or action == UNLABELLED:
        raise ValueError(f"unknown AI action {action!r}")
    if source not in CALL_SOURCES:
        raise ValueError(f"unknown AI call source {source!r}")
    return validate_subject(subject)


# ---- where the logs are ------------------------------------------------------------------------------


def app_log_path(config: Config) -> Path:
    """The app's log: ``claude.usage_log`` inside data_dir (``<data_dir>/logs/ai_usage.jsonl``)."""
    path = Path(config.claude.usage_log)
    return path if path.is_absolute() else config.storage.data_dir / path


def run_log_path(config: Config) -> Path:
    """The eval and live log: ``claude.run_usage_log``, relative to the repository (``reports/…``)."""
    path = Path(config.claude.run_usage_log)
    return path if path.is_absolute() else config.repo_root / path


def _label(config: Config, which: str) -> str:
    """How the page names a log: the configured path, as written in config/project.yaml."""
    raw = config.claude.usage_log if which == "app" else config.claude.run_usage_log
    return Path(raw).as_posix()


# ---- writing -----------------------------------------------------------------------------------------


def call_entry(
    *,
    skill: str,
    backend: str,
    action: str,
    subject: Subject,
    source: str,
    duration_s: float,
    cost_usd: float | None,
    ok: bool,
    error: str | None,
) -> dict[str, Any]:
    """One log line, fields in ``FIELDS`` order. Only these fields exist; there is no room for content."""
    return {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "skill": skill,
        "backend": backend,
        "action": action,
        "subject": subject,
        "source": source,
        "duration_s": round(duration_s, 2),
        "total_cost_usd": cost_usd,
        "ok": ok,
        "error": error,
    }


def write_line(config: Config, entry: dict[str, Any]) -> None:
    """Append ``entry`` to the log its source belongs in. A log that can't be written is skipped.

    App lines are written only when data_dir exists (the data folder is never created here).
    """
    source = entry["source"]
    if source == "app":
        if not config.storage.data_dir.is_dir():
            return
        path = app_log_path(config)
    else:
        path = run_log_path(config)
    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
    except OSError as err:
        log.warning("The AI usage line could not be written to %s: %s", path, err.strerror or err)


# ---- reading -----------------------------------------------------------------------------------------


class _Line(BaseModel):
    """A stored line, checked strictly (JSON types, no coercion).

    Refused: extra fields, a skill or backend that isn't a name, non-finite or negative numbers, wrong types.
    The subject is checked separately (IDs only)."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)

    at: AwareDatetime
    skill: Annotated[str, StringConstraints(pattern=SLUG_PATTERN)]
    backend: Literal["cli"] | None = None
    action: str
    subject: dict[str, Any]
    source: str
    duration_s: float | None = Field(default=None, ge=0)
    total_cost_usd: float | None = Field(default=None, ge=0)
    ok: StrictBool
    error: str | None = None
    imported: StrictBool = False


@dataclass(frozen=True)
class UsageLine:
    """One logged call, as the page and the totals use it."""

    at: datetime
    skill: str
    action: str
    subject: Subject
    source: str
    duration_s: float | None
    cost_usd: float | None
    ok: bool
    error: str | None
    imported: bool = False


@dataclass(frozen=True)
class LogProblem:
    """A line (or a whole file, ``line`` None) that could not be read; it is left out of everything."""

    file: str
    line: int | None
    message: str


@dataclass
class UsageLog:
    """Both logs read together: lines newest first, problems, and old run-log lines not yet imported."""

    lines: list[UsageLine] = field(default_factory=list)
    problems: list[LogProblem] = field(default_factory=list)
    legacy_pending: int = 0


@dataclass
class _FileRead:
    lines: list[UsageLine] = field(default_factory=list)
    problems: list[LogProblem] = field(default_factory=list)
    legacy: int = 0
    imported: int = 0


def _parse(raw_json: str, allowed_sources: frozenset[str]) -> UsageLine:
    """A stored line as a ``UsageLine``; ``ValueError`` with a plain-word reason if it is not valid.

    Reasons name the field, never its value: a hand-edited line could hold any text (data-integrity review).
    """
    try:
        raw = _Line.model_validate_json(raw_json)
    except ValidationError as exc:
        first = exc.errors()[0]
        if first["type"] == "extra_forbidden":
            raise ValueError("it has an unexpected field") from None
        where = str(first["loc"][0]) if first["loc"] else "a"
        raise ValueError(f"the {where} field is missing or invalid") from None
    if raw.action not in ACTIONS:
        raise ValueError("the action is unknown")
    if raw.source not in allowed_sources:
        raise ValueError("its source does not belong in this log")
    if raw.error is not None and raw.error not in ERROR_LABELS:
        raise ValueError("the error kind is unknown")
    if raw.ok != (raw.error is None):
        raise ValueError("ok and error disagree")
    if len({raw.action == UNLABELLED, raw.imported, raw.source == "unknown"}) != 1:
        raise ValueError("only imported lines may be unlabelled or from an unknown source")
    try:
        subject = validate_subject(raw.subject)
    except ValueError:
        raise ValueError("the subject holds something other than IDs") from None
    return UsageLine(
        at=raw.at.astimezone(UTC),
        skill=raw.skill,
        action=raw.action,
        subject=subject,
        source=raw.source,
        duration_s=raw.duration_s,
        cost_usd=raw.total_cost_usd,
        ok=raw.ok,
        error=raw.error,
        imported=raw.imported,
    )


def _read_file(path: Path, label: str, allowed_sources: frozenset[str], *, legacy_ok: bool) -> _FileRead:
    """Read one log. In the run log, a line without ``source`` is an old line (counted, not shown)."""
    out = _FileRead()
    if not path.exists():
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        out.problems.append(LogProblem(label, None, "the file could not be read"))
        return out
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            out.problems.append(LogProblem(label, number, "not valid JSON"))
            continue
        if not isinstance(obj, dict):
            out.problems.append(LogProblem(label, number, "not a log entry"))
            continue
        if legacy_ok and "source" not in obj:
            out.legacy += 1
            continue
        try:
            line = _parse(raw, allowed_sources)
        except ValueError as err:
            out.problems.append(LogProblem(label, number, str(err)))
            continue
        out.lines.append(line)
        out.imported += line.imported
    return out


def read_all(config: Config) -> UsageLog:
    """Both logs, newest first. Old run-log lines count as pending until the app log holds imported lines."""
    app = _read_file(app_log_path(config), _label(config, "app"), APP_LOG_SOURCES, legacy_ok=False)
    run = _read_file(run_log_path(config), _label(config, "run"), RUN_LOG_SOURCES, legacy_ok=True)
    lines = sorted([*app.lines, *run.lines], key=lambda x: x.at, reverse=True)
    pending = run.legacy if app.imported == 0 and not any(p.line is None for p in app.problems) else 0
    return UsageLog(lines=lines, problems=[*app.problems, *run.problems], legacy_pending=pending)


# ---- totals ------------------------------------------------------------------------------------------


@dataclass
class Tally:
    """Calls, failed calls, summed estimated cost and calls that reported no cost (counted as $0)."""

    calls: int = 0
    errors: int = 0
    cost_usd: float = 0.0
    no_cost: int = 0

    def add(self, line: UsageLine) -> None:
        self.calls += 1
        self.errors += not line.ok
        if line.cost_usd is None:
            self.no_cost += 1
        else:
            self.cost_usd += line.cost_usd


@dataclass
class Pair:
    """The same tally for the current month and for all time."""

    month: Tally = field(default_factory=Tally)
    all_time: Tally = field(default_factory=Tally)


@dataclass
class Totals:
    month: str  # "YYYY-MM" of the current month in the display zone
    by_action: dict[str, Pair]
    by_source: dict[str, Pair]
    overall: Pair


def month_key(at: datetime, tz: str) -> str:
    """The calendar month ("YYYY-MM") an instant falls in, in the display zone."""
    return at.astimezone(ZoneInfo(tz)).strftime("%Y-%m")


def totals(lines: Iterable[UsageLine], now: datetime, tz: str) -> Totals:
    """Totals for the month containing ``now`` and for all time, by action, by source and overall.

    The month is the display zone's: a call at 02:00 UTC on 1 October is September in New York.
    """
    current = month_key(now, tz)
    by_action: dict[str, Pair] = {}
    by_source: dict[str, Pair] = {}
    overall = Pair()
    for line in lines:
        in_month = month_key(line.at, tz) == current
        for pair in (
            by_action.setdefault(line.action, Pair()),
            by_source.setdefault(line.source, Pair()),
            overall,
        ):
            pair.all_time.add(line)
            if in_month:
                pair.month.add(line)
    return Totals(
        month=current,
        by_action={k: by_action[k] for k in ACTIONS if k in by_action},
        by_source={k: by_source[k] for k in SOURCES if k in by_source},
        overall=overall,
    )


# ---- one-time import of the old single log (hub import-usage) -------------------------------------------


class ImportRefused(Exception):
    """The import cannot run (no data folder, or the app log cannot be read)."""


@dataclass(frozen=True)
class ImportResult:
    imported: int
    already_done: bool
    unreadable: int


class _OldLine(BaseModel):
    """A line of reports/ai_usage.jsonl written before DECISIONS #60 (no action, subject or source)."""

    model_config = ConfigDict(extra="forbid")

    at: AwareDatetime
    skill: str
    backend: str | None = None
    duration_s: float | None = Field(default=None, ge=0)
    total_cost_usd: float | None = Field(default=None, ge=0)
    ok: StrictBool
    error: str | None = None


def import_legacy(config: Config) -> ImportResult:
    """Copy the old run-log lines into the app log once, as ``unlabelled`` from source ``unknown``.

    Old lines mix eval runs, live checks and app clicks and can't be told apart, so none is guessed.
    The run log is never changed (its old lines are then ignored by the page). Running it again does nothing.
    """
    data_dir = config.storage.data_dir
    if not data_dir.is_dir():
        raise ImportRefused(f"The data folder {data_dir} does not exist.")
    app = app_log_path(config)
    if app.exists():
        try:
            existing = app.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as err:
            raise ImportRefused(f"{_label(config, 'app')} could not be read ({err}).") from None
        for raw in existing.splitlines():
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj.get("imported") is True:
                return ImportResult(imported=0, already_done=True, unreadable=0)
    run = run_log_path(config)
    if not run.exists():
        return ImportResult(imported=0, already_done=False, unreadable=0)
    try:
        old_text = run.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        raise ImportRefused(f"{_label(config, 'run')} could not be read ({err}).") from None
    entries: list[str] = []
    unreadable = 0
    for raw in old_text.splitlines():
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            unreadable += 1
            continue
        if not isinstance(obj, dict) or "source" in obj:
            unreadable += not isinstance(obj, dict)
            continue
        try:
            old = _OldLine.model_validate(obj)
        except ValidationError:
            unreadable += 1
            continue
        if old.error is not None and old.error not in ERROR_LABELS:
            unreadable += 1
            continue
        entry = {
            "at": obj["at"],
            "skill": old.skill,
            "backend": old.backend,
            "action": UNLABELLED,
            "subject": {},
            "source": "unknown",
            "duration_s": old.duration_s,
            "total_cost_usd": old.total_cost_usd,
            "ok": old.ok,
            "error": old.error,
            "imported": True,
        }
        entries.append(json.dumps(entry))
    if entries:
        with _LOCK:
            app.parent.mkdir(parents=True, exist_ok=True)
            with app.open("a", encoding="utf-8") as fh:
                fh.write("".join(e + "\n" for e in entries))
    return ImportResult(imported=len(entries), already_done=False, unreadable=unreadable)
