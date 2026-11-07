"""The Add / Edit programme form (docs/03 §1, docs/01 §3): parsing, row identity and validation.

Field names: top-level fields by name; repeatable rows as ``<group>-<i>-<field>``; a row with
``<group>-<i>-remove`` is dropped; a submit ``_add=<group>`` asks for one more empty row (nothing saved).

Rules enforced here:
- New programmes are always ``unverified`` and every new deadline is ``unverified``; a submitted
  ``verification`` or ``verified_on`` is ignored (only the Mark verified action sets them, docs/02 §6).
- Editing a deadline's date, time, zone or source URL resets that deadline to ``unverified``
  (DECISIONS #21, #30).
- At least one deadline, each with a time zone and a source URL (DECISIONS #13, rule 10).
- Rows keep their identity by a hidden id, so done flags, document status and verification survive.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ValidationError
from starlette.datastructures import FormData

from hub.config import Config
from hub.models.common import Verification
from hub.models.programme import Programme
from hub.rules.names import TRANSLITERATE
from hub.store import format_loc

# group → (fields typed by the user, hidden identity fields, singular noun for buttons and labels)
GROUPS: dict[str, tuple[tuple[str, ...], tuple[str, ...], str]] = {
    "sources": (("url", "title", "checked"), (), "source"),
    "deadlines": (("label", "date", "time", "tz", "time_unknown", "source_url"), ("id",), "deadline"),
    "requirements": (("label", "note"), ("id",), "requirement"),
    "steps": (("label", "note"), ("order",), "step"),
    "documents": (("label", "method", "note"), ("id",), "document"),
    "essays": (("title", "prompt", "word_limit"), ("id",), "essay"),
    "courses": (("name", "description"), (), "course"),
}
TOP_FIELDS = (
    "id",
    "name",
    "short_name",
    "school",
    "unit",
    "aliases",
    "status",
    "verification_note",
    "portal_url",
    "letters_required",
    "letters_note",
    "ai_policy_text",
    "ai_policy_source_url",
    "fee_amount",
    "fee_currency",
    "fee_note",
    "curriculum_summary",
)
MULTILINE = {
    "aliases",
    "prompt",
    "description",
    "curriculum_summary",
    "ai_policy_text",
    "letters_note",
    "note",
}
_ROW_KEY = re.compile(r"^(?P<group>[a-z]+)-(?P<index>\d{1,3})-(?P<field>[a-z_]+)$")
SLUG_RE = re.compile(r"^[a-z0-9-]{2,60}$")


@dataclass
class FormState:
    """What the form shows: string values of every field and row, plus errors (field path → message)."""

    fields: dict[str, str]
    rows: dict[str, list[dict[str, str]]]
    errors: list[tuple[str, str]] = field(default_factory=list)

    def has_error(self, path_prefix: str) -> bool:
        paths = [p for p, _ in self.errors] + [p.replace(".", "_") for p, _ in self.errors]
        return any(p == path_prefix or p.startswith(path_prefix + ".") for p in paths)


def slugify(text: str, fallback: str = "item") -> str:
    """Lowercase ASCII slug ``[a-z0-9-]`` (e.g. "Résumé / CV" → "resume-cv", "Ødegaard" → "odegaard"),
    at most 60 characters."""
    ascii_text = (
        unicodedata.normalize("NFKD", text.translate(TRANSLITERATE))
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")[:60].strip("-")
    return slug if len(slug) >= 2 else fallback


def unique_slug(base: str, taken: set[str]) -> str:
    slug, n = base, 2
    while slug in taken:
        suffix = f"-{n}"
        slug, n = base[: 60 - len(suffix)] + suffix, n + 1
    taken.add(slug)
    return slug


def _s(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# ---- programme → form ---------------------------------------------------------------------------------


def blank_row(group: str) -> dict[str, str]:
    typed, hidden, _ = GROUPS[group]
    return dict.fromkeys((*hidden, *typed), "")


def state_from_programme(p: Programme) -> FormState:
    """The form pre-filled from a stored programme; deadline date and time are its wall-clock values."""
    fields = {
        "id": p.id,
        "name": p.name,
        "short_name": p.short_name,
        "school": p.school,
        "unit": _s(p.unit),
        "aliases": "\n".join(p.aliases),
        "status": p.status.value,
        "verification_note": _s(p.verification_note),
        "portal_url": _s(p.portal_url),
        "letters_required": _s(p.letters_required),
        "letters_note": _s(p.letters_note),
        "ai_policy_text": _s(p.ai_policy.text),
        "ai_policy_source_url": _s(p.ai_policy.source_url),
        "fee_amount": _s(p.fee.amount),
        "fee_currency": p.fee.currency,
        "fee_note": _s(p.fee.note),
        "curriculum_summary": _s(p.curriculum.summary),
    }
    rows = {
        "sources": [{"url": s.url, "title": s.title, "checked": _s(s.checked)} for s in p.sources],
        "deadlines": [
            {
                "id": d.id,
                "label": d.label,
                "date": d.due_local.strftime("%Y-%m-%d"),
                "time": d.due_local.strftime("%H:%M"),
                "tz": d.tz,
                "time_unknown": "" if d.time_known else "1",
                "source_url": d.source_url,
            }
            for d in p.deadlines
        ],
        "requirements": [{"id": r.id, "label": r.label, "note": _s(r.note)} for r in p.requirements],
        "steps": [{"order": str(s.order), "label": s.label, "note": _s(s.note)} for s in p.steps],
        "documents": [
            {"id": d.id, "label": d.label, "method": _s(d.method), "note": _s(d.note)} for d in p.documents
        ],
        "essays": [
            {"id": e.id, "title": e.title, "prompt": _s(e.prompt), "word_limit": _s(e.word_limit)}
            for e in p.essays
        ],
        "courses": [{"name": c.name, "description": _s(c.description)} for c in p.curriculum.courses],
    }
    return FormState(fields, rows)


def new_state(config: Config) -> FormState:
    """An empty Add programme form: one source row and one deadline row to start from."""
    fields = dict.fromkeys(TOP_FIELDS, "")
    fields["status"] = "not_started"
    fields["fee_currency"] = "USD"
    rows = {group: [] for group in GROUPS}
    rows["sources"] = [blank_row("sources")]
    rows["deadlines"] = [blank_row("deadlines")]
    return FormState(fields, rows)


# ---- form → state -------------------------------------------------------------------------------------


def _clean(name: str, value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")  # browsers submit textarea lines as CRLF
    return value if name in MULTILINE else value.strip()


def state_from_form(form: FormData) -> tuple[FormState, str | None]:
    """Read the submitted form; returns the state (removed rows dropped) and the ``_add`` group, if any."""
    fields = {name: _clean(name, str(form.get(name, ""))) for name in TOP_FIELDS}
    indexed: dict[str, dict[int, dict[str, str]]] = {g: {} for g in GROUPS}
    removed: set[tuple[str, int]] = set()
    for key, value in form.multi_items():
        match = _ROW_KEY.match(key)
        if not match or match["group"] not in GROUPS:
            continue
        group, index, name = match["group"], int(match["index"]), match["field"]
        if name == "remove":
            if value:
                removed.add((group, index))
            continue
        typed, hidden, _ = GROUPS[group]
        if name in typed or name in hidden:
            indexed[group].setdefault(index, blank_row(group))[name] = _clean(name, str(value))
    rows = {
        group: [row for index, row in sorted(by_index.items()) if (group, index) not in removed]
        for group, by_index in indexed.items()
    }
    add = str(form.get("_add", "")) or None
    if add in GROUPS:
        rows[add].append(blank_row(add))
    else:
        add = None
    return FormState(fields, rows), add


def _is_blank(group: str, row: dict[str, str]) -> bool:
    typed, _, _ = GROUPS[group]
    return not any(row.get(name) for name in typed if name != "time_unknown")


# ---- state → programme --------------------------------------------------------------------------------


class _Errors:
    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []

    def add(self, path: str, message: str) -> None:
        self.items.append((path, message))


def _opt(value: str) -> str | None:
    return value or None


def _int(value: str, path: str, errors: _Errors) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        errors.add(path, "must be a whole number")
        return None


def _date(value: str, path: str, errors: _Errors) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        errors.add(path, "must be a date (YYYY-MM-DD)")
        return None


def _zone_ok(tz: str) -> bool:
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def build_programme(
    state: FormState, existing: Programme | None, taken_ids: set[str], config: Config
) -> tuple[Programme | None, list[tuple[str, str]]]:
    """Turn the form into a validated programme, or return the errors (path, message) to show inline.

    ``existing`` is the stored programme when editing (its id, verification, interviews, done flags,
    document statuses and deadline verification are kept); None when adding. ``taken_ids`` are the
    programme IDs already in use.
    """
    errors = _Errors()
    f = state.fields
    rows = {g: [r for r in state.rows[g] if not _is_blank(g, r)] for g in GROUPS}

    if existing is None:
        pid = f["id"] or slugify(f["short_name"] or f["name"], fallback="")
        if not f["id"] and not pid:
            if f["short_name"] or f["name"]:  # typed, but no letters or digits to build an ID from
                errors.add(
                    "id",
                    "cannot be made from the short name (it needs at least 2 letters or digits); enter one",
                )
            # else nothing to derive the ID from yet: the missing names are reported instead
        elif not SLUG_RE.fullmatch(pid):
            errors.add("id", "use 2–60 lowercase letters, digits or hyphens (or leave it blank)")
        elif pid in taken_ids:
            errors.add("id", f"a programme with the ID {pid} already exists; choose another")
    else:
        pid = existing.id
    for name in ("name", "short_name", "school"):
        if not f[name]:
            errors.add(name, "is required")

    # deadlines (DECISIONS #13, rule 10)
    old_deadlines = {d.id: d for d in existing.deadlines} if existing else {}
    deadline_ids = set(old_deadlines)
    deadlines: list[dict[str, Any]] = []
    if not rows["deadlines"]:
        errors.add("deadlines", "add at least one deadline, with its date, time zone and source URL")
    for i, row in enumerate(rows["deadlines"]):
        path = f"deadlines[{i}]"
        if not row["label"]:
            errors.add(f"{path}.label", "enter a label, e.g. Application deadline")
        day = _date(row["date"], f"{path}.date", errors)
        if not row["date"]:
            errors.add(f"{path}.date", "enter the deadline date")
        time_known = bool(row["time"]) and not row["time_unknown"]
        clock = row["time"] or config.time.assumed_deadline_time
        try:
            hour, minute = (int(x) for x in clock.split(":"))
            due_local = datetime(day.year, day.month, day.day, hour, minute) if day else None
        except ValueError:
            errors.add(f"{path}.time", "must be a time (HH:MM)")
            due_local = None
        if not row["tz"]:
            errors.add(f"{path}.tz", "enter the school's time zone, e.g. America/New_York")
        elif not _zone_ok(row["tz"]):
            errors.add(f"{path}.tz", f"{row['tz']} is not an IANA time zone (e.g. America/New_York)")
        if not row["source_url"]:
            errors.add(f"{path}.source_url", "enter the page this deadline comes from")
        old = old_deadlines.get(row["id"]) if existing else None
        if old is None:
            row_id = unique_slug(slugify(row["label"] or "deadline"), deadline_ids)
            verification = Verification.UNVERIFIED
        else:
            row_id = old.id
            # date, time or zone (docs/02 §6) or its source (DECISIONS #30); time_known alone does not
            unchanged = (
                old.due_local == due_local and old.tz == row["tz"] and old.source_url == row["source_url"]
            )
            verification = old.verification if unchanged else Verification.UNVERIFIED
        deadlines.append(
            {
                "id": row_id,
                "label": row["label"],
                "due_local": due_local,
                "tz": row["tz"],
                "time_known": time_known,
                "verification": verification,
                "source_url": row["source_url"],
            }
        )

    def keyed(
        group: str, title_field: str, old_items: dict[str, Any]
    ) -> list[tuple[str, dict[str, str], Any]]:
        ids = set(old_items)
        out = []
        for row in rows[group]:
            old = old_items.get(row.get("id", ""))
            row_id = (
                old.id if old is not None else unique_slug(slugify(row[title_field], GROUPS[group][2]), ids)
            )
            out.append((row_id, row, old))
        return out

    requirements = [
        {"id": rid, "label": row["label"], "note": _opt(row["note"]), "done": old.done if old else False}
        for rid, row, old in keyed(
            "requirements", "label", {r.id: r for r in existing.requirements} if existing else {}
        )
    ]
    for i, row in enumerate(rows["requirements"]):
        if not row["label"]:
            errors.add(f"requirements[{i}].label", "enter the requirement")
    old_docs = {d.id: d for d in existing.documents} if existing else {}
    documents = []
    for i, (did, row, old) in enumerate(keyed("documents", "label", old_docs)):
        if not row["label"]:
            errors.add(f"documents[{i}].label", "enter the document")
        documents.append(
            {
                "id": did,
                "label": row["label"],
                "method": _opt(row["method"]),
                "note": _opt(row["note"]),
                "status": old.status if old else "not_requested",
                "updated": old.updated if old else None,
            }
        )
    old_essays = {e.id: e for e in existing.essays} if existing else {}
    essays = []
    for i, (eid, row, _old) in enumerate(keyed("essays", "title", old_essays)):
        if not row["title"]:
            errors.add(f"essays[{i}].title", "enter the essay title")
        limit = _int(row["word_limit"], f"essays[{i}].word_limit", errors)
        essays.append({"id": eid, "title": row["title"], "prompt": _opt(row["prompt"]), "word_limit": limit})
    old_steps = {s.order: s for s in existing.steps} if existing else {}
    steps = []
    kept_orders = {int(r["order"]) for r in rows["steps"] if r.get("order", "").isdigit()} & set(old_steps)
    next_order = max([*old_steps, 0]) + 1
    for i, row in enumerate(rows["steps"]):
        if not row["label"]:
            errors.add(f"steps[{i}].label", "enter the step")
        old = old_steps.get(int(row["order"])) if row.get("order", "").isdigit() else None
        if old is not None and old.order in kept_orders:
            order = old.order  # the order is the step's identity (/steps/{order}); never renumbered
            kept_orders.discard(old.order)
        else:
            order, next_order = next_order, next_order + 1
            old = None
        steps.append(
            {
                "order": order,
                "label": row["label"],
                "note": _opt(row["note"]),
                "done": old.done if old else False,
            }
        )
    sources = []
    for i, row in enumerate(rows["sources"]):
        if not row["url"]:
            errors.add(f"sources[{i}].url", "enter the source URL")
        sources.append(
            {
                "url": row["url"],
                "title": row["title"] or row["url"],
                "checked": _date(row["checked"], f"sources[{i}].checked", errors),
            }
        )
    courses = [{"name": r["name"], "description": _opt(r["description"])} for r in rows["courses"]]
    for i, row in enumerate(rows["courses"]):
        if not row["name"]:
            errors.add(f"courses[{i}].name", "enter the course name")

    fee_amount = None
    if f["fee_amount"]:
        try:
            fee_amount = float(f["fee_amount"])
            if not math.isfinite(fee_amount):
                raise ValueError
        except ValueError:
            errors.add("fee.amount", "must be a number")
            fee_amount = None
    data: dict[str, Any] = {
        "id": pid,
        "name": f["name"],
        "short_name": f["short_name"],
        "school": f["school"],
        "unit": _opt(f["unit"]),
        "official_domains": list(existing.official_domains) if existing else [],  # not on the form (#37)
        "aliases": [a.strip() for a in f["aliases"].split("\n") if a.strip()],  # one per line: commas kept
        "status": f["status"] or "not_started",
        "verification": existing.verification if existing else Verification.UNVERIFIED,
        "verification_note": _opt(f["verification_note"]),
        "verified_on": existing.verified_on if existing else None,
        "portal_url": _opt(f["portal_url"]),
        "sources": sources,
        "deadlines": deadlines,
        "letters_required": _int(f["letters_required"], "letters_required", errors),
        "letters_note": _opt(f["letters_note"]),
        "requirements": requirements,
        "steps": steps,
        "documents": documents,
        "interviews": [i.model_dump() for i in existing.interviews] if existing else [],
        "essays": essays,
        "curriculum": {"summary": _opt(f["curriculum_summary"]), "courses": courses},
        "ai_policy": {"text": _opt(f["ai_policy_text"]), "source_url": _opt(f["ai_policy_source_url"])},
        "fee": {"amount": fee_amount, "currency": f["fee_currency"] or "USD", "note": _opt(f["fee_note"])},
    }
    if errors.items:
        return None, errors.items
    if existing is not None:  # keep the file's shape: don't add defaulted keys the file never had
        data = trim_like(data, existing)
    try:
        return Programme.model_validate(data), []
    except ValidationError as exc:
        return None, [(format_loc(tuple(e["loc"])) or "form", _plain_message(e)) for e in exc.errors()]


def _plain_message(error: Any) -> str:
    """A validation message in words: never a regex or a Pydantic type name (docs/03 §5)."""
    field = str(error["loc"][-1]) if error["loc"] else ""
    if error["type"] == "string_pattern_mismatch":
        if field in ("url", "source_url", "portal_url"):
            return "must be a web address starting with http:// or https://"
        if field == "id":
            return "use 2–60 lowercase letters, digits or hyphens"
        return "has characters that are not allowed"
    if error["type"] in ("greater_than_equal", "greater_than"):
        limit = error.get("ctx", {}).get("ge", error.get("ctx", {}).get("gt"))
        if isinstance(limit, float) and limit.is_integer():
            limit = int(limit)
        return f"must be at least {limit}" if limit is not None else "is too small"
    if error["type"] == "missing":
        return "is required"
    if error["type"].startswith(("int_", "float_", "finite_")):
        return "must be a number"
    if error["type"] == "value_error":
        return str(error["msg"]).removeprefix("Value error, ")  # our own validators' wording
    return "is not valid"


_ITEM_KEYS = ("id", "order", "url")  # how list items are matched to the stored ones (else by position)


def trim_like(data: dict[str, Any], old: BaseModel) -> dict[str, Any]:
    """Drop keys the stored model never had in its file and whose new value is still the default.

    Applied recursively to nested models and to list items matched to their stored counterpart, so
    saving an unchanged form never adds ``note: null``-style keys to a hand-written file.
    """
    out: dict[str, Any] = {}
    fields = type(old).model_fields
    for name, value in data.items():
        info = fields.get(name)
        if info is not None and name not in old.model_fields_set and value == _default_dump(info):
            continue
        current = getattr(old, name, None)
        if isinstance(value, dict) and isinstance(current, BaseModel):
            value = trim_like(value, current)
        elif isinstance(value, list) and isinstance(current, list):
            value = [_trim_item(item, n, current) for n, item in enumerate(value)]
        out[name] = value
    return out


def _trim_item(item: Any, index: int, olds: list[Any]) -> Any:
    if not isinstance(item, dict) or not olds or not isinstance(olds[0], BaseModel):
        return item
    key = next((k for k in _ITEM_KEYS if k in item and hasattr(olds[0], k)), None)
    if key is not None:
        match = next((o for o in olds if getattr(o, key) == item[key]), None)
    else:
        match = olds[index] if index < len(olds) else None
    return trim_like(item, match) if match is not None else item


def _default_dump(info: Any) -> Any:
    value = info.get_default(call_default_factory=True)
    return value.model_dump() if hasattr(value, "model_dump") else value


FIELD_LABELS = {
    "id": "Programme ID",
    "name": "Programme name",
    "short_name": "Short name",
    "school": "School",
    "unit": "Unit",
    "aliases": "Aliases",
    "status": "Status",
    "verification_note": "Why unverified",
    "portal_url": "Application portal URL",
    "letters_required": "Letters required",
    "letters_note": "Letters note",
    "fee.amount": "Fee amount",
    "fee.currency": "Fee currency",
    "fee.note": "Fee note",
    "ai_policy.text": "AI-use policy",
    "ai_policy.source_url": "AI-use policy source URL",
    "curriculum.summary": "Curriculum summary",
    "deadlines": "Deadlines",
    "sources": "Sources",
    "form": "Form",
    "file": "Data file",
}
ROW_FIELD_LABELS = {
    "label": "Label",
    "date": "Date",
    "time": "Time",
    "tz": "Time zone",
    "source_url": "Source URL",
    "due_local": "Date and time",
    "url": "URL",
    "title": "Title",
    "checked": "Checked on",
    "note": "Note",
    "method": "How sent",
    "word_limit": "Word limit",
    "prompt": "Prompt",
    "name": "Course",
}
_PATH = re.compile(r"^(?P<group>[a-z_]+)\[(?P<index>\d+)\](?:\.(?P<field>[a-z_]+))?")


def field_label(path: str) -> str:
    """A form error path in words: ``deadlines[0].tz`` → "Deadline 1 · Time zone"."""
    if path in FIELD_LABELS:
        return FIELD_LABELS[path]
    match = _PATH.match(path)
    if match and match["group"] in GROUPS:
        noun = GROUPS[match["group"]][2].capitalize()
        where = f"{noun} {int(match['index']) + 1}"
        return f"{where} · {ROW_FIELD_LABELS.get(match['field'] or '', match['field'] or '')}".rstrip(" ·")
    return path.replace("_", " ").capitalize()
