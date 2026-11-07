"""Programme research and re-research, the Python half (docs/04 §7, §7.1; DECISIONS #31, #37; rule 13).

The ``/research-programme`` skill browses official pages and proposes a programme in which every field
carries the URL it came from. Everything that must be exact happens here, never in the model:

- **Official sources.** A source counts only if its host equals one of the user-confirmed
  ``official_domains`` or is a subdomain of one on a dot boundary, or is on
  ``config.research.official_shared_domains`` (mba.com, GMAC). Anything else is refused, naming the field.
- **Domains.** Proposed domains are never used until the user confirms them; a single-label name or a public
  suffix (edu, ac.uk, github.io …; offline list from ``publicsuffixlist``) is refused.
- **Unverified.** Researched data is always unverified (rule 11): a new programme is built unverified, and
  re-research never sets ``verified``. An accepted change to a deadline's date, time, zone or source resets
  that deadline (DECISIONS #21, #30); any other accepted change makes the programme unverified.
- **Nothing removed.** Re-research never deletes stored data; a stored value research didn't find is
  shown as "kept".
- **Payloads carry programme data only** (the call has web tools, so fetched pages could steer it).
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from typing import Any
from urllib.parse import urlsplit

import jsonschema
from publicsuffixlist import PublicSuffixList
from pydantic import TypeAdapter, ValidationError

from hub.ai.skills import load_schema
from hub.models.common import LocalDateTime, Verification
from hub.models.programme import (
    AiPolicy,
    Course,
    Curriculum,
    Deadline,
    EssayPrompt,
    Fee,
    Programme,
    Requirement,
    Source,
)
from hub.programme_form import slugify, unique_slug

NO_OFFICIAL_SOURCE = "No official source found"
RESEARCHED_NOTE = "Researched, not checked"  # docs/02 §3 badge reason
SAVE_NEEDS_DEADLINE = "Save needs at least one deadline with a time zone and an official source URL."
MISSING_SOURCE = "missing; every filled field needs an official source"
NO_DOMAIN = "Enter at least one official domain."

_DOMAIN_RE = re.compile(r"^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}$")  # as models.common.Domain
_LOCAL: TypeAdapter = TypeAdapter(LocalDateTime)
_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")

# Top-level fields with one value and one source: (output key, label shown to the user).
SCALARS: tuple[tuple[str, str], ...] = (
    ("name", "Name"),
    ("short_name", "Short name"),
    ("school", "School"),
    ("unit", "Unit"),
    ("aliases", "Aliases"),
    ("portal_url", "Portal"),
    ("letters_required", "Letters required"),
    ("letters_note", "Letters note"),
    ("curriculum_summary", "Curriculum summary"),
    ("ai_policy", "AI policy"),
)
REQUIRED_SCALARS = ("name", "short_name", "school")  # a programme can't be saved without these
DEADLINE_FIELD_LABELS = {
    "label": "Label",
    "due_local": "Date and time",
    "tz": "Time zone",
    "source_url": "Source URL",
}


@cache
def _psl() -> PublicSuffixList:
    return PublicSuffixList()  # bundled list, no network


# ---- payloads (programme data only) --------------------------------------------------------------------


def research_payload(query: str, url: str | None, today: date) -> dict[str, Any]:
    """The ``/research-programme`` input for a new programme: the query, an optional start URL, today."""
    return {"query": query, "url": url or None, "today": today.isoformat()}


def reresearch_payload(programme: Programme, today: date) -> dict[str, Any]:
    """The input for re-research: only the fields research compares (DECISIONS #66). Status, steps,
    documents, interviews, verification and every free-text note (requirement notes, ``letters_note``,
    ``fee.note``) stay local: they may be the owner's own text, and this call has web tools. The comparison
    itself still runs in Python against the full local file."""
    p = programme
    return {
        "programme_id": p.id,
        "current": {
            "id": p.id,
            "name": p.name,
            "short_name": p.short_name,
            "school": p.school,
            "unit": p.unit,
            "official_domains": list(p.official_domains),
            "aliases": list(p.aliases),
            "portal_url": p.portal_url,
            "deadlines": [
                {
                    "id": d.id,
                    "label": d.label,
                    "due_local": d.due_local.strftime("%Y-%m-%dT%H:%M"),
                    "tz": d.tz,
                    "time_known": d.time_known,
                    "source_url": d.source_url,
                }
                for d in p.deadlines
            ],
            "letters_required": p.letters_required,
            "requirements": [{"id": r.id, "label": r.label} for r in p.requirements],
            "essays": [
                {"id": e.id, "title": e.title, "prompt": e.prompt, "word_limit": e.word_limit}
                for e in p.essays
            ],
            "curriculum": {
                "summary": p.curriculum.summary,
                "courses": [{"name": c.name, "description": c.description} for c in p.curriculum.courses],
            },
            "ai_policy": {"text": p.ai_policy.text, "source_url": p.ai_policy.source_url},
            "fee": {"amount": p.fee.amount, "currency": p.fee.currency},
            "sources": [{"url": s.url, "title": s.title} for s in p.sources],
        },
        "today": today.isoformat(),
    }


# ---- hosts and domains ---------------------------------------------------------------------------------


def host_of(url: str) -> str | None:
    """The lower-case host of an http(s) URL, or None for anything else (no scheme, userinfo, an IP)."""
    try:
        parts = urlsplit((url or "").strip())
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc or "@" in parts.netloc:
        return None
    host = (host or "").rstrip(".")
    return host if _DOMAIN_RE.fullmatch(host) else None


def _within(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def is_official(url: str, domains: Sequence[str], shared: Sequence[str]) -> bool:
    """True if ``url``'s host is one of ``domains`` or ``shared``, or a subdomain on a dot boundary."""
    host = host_of(url)
    return host is not None and any(_within(host, d) for d in (*domains, *shared))


def check_domains(raw: Sequence[str]) -> tuple[list[str], list[str]]:
    """Clean the user's official domains: lower-case, deduplicated; refuse single labels, public suffixes
    and anything that isn't a domain name. Returns (accepted domains, problems)."""
    domains: list[str] = []
    problems: list[str] = []
    seen_any = False
    for item in raw:
        text = item.strip()
        if not text:
            continue
        seen_any = True
        d = text.lower().rstrip(".")
        if not _DOMAIN_RE.fullmatch(d):
            if _LABEL_RE.fullmatch(d):
                problems.append(f"{d} has a single label; enter the school's own domain, e.g. princeton.edu")
            else:
                problems.append(f"{text} is not a domain name; enter one like princeton.edu")
        elif _psl().is_public(d):
            problems.append(f"{d} is a public suffix, not a school's own domain")
        elif d not in domains:
            domains.append(d)
    if not seen_any:
        problems.append(NO_DOMAIN)
    return domains, problems


def split_domains(text: str) -> list[str]:
    """A textarea's domains: one per line (commas and spaces also separate)."""
    return [d for d in re.split(r"[\s,]+", text or "") if d]


def prefill_domains(programme: Programme) -> list[str]:
    """Hosts of every URL the programme already stores (``www.`` dropped), for the user to confirm."""
    urls = [s.url for s in programme.sources] + [d.source_url for d in programme.deadlines]
    urls += [u for u in (programme.portal_url, programme.ai_policy.source_url) if u]
    hosts: list[str] = []
    for url in urls:
        host = host_of(url)
        if host:
            host = host.removeprefix("www.")
            if host not in hosts:
                hosts.append(host)
    return hosts


# ---- the proposal and its checks -------------------------------------------------------------------------


def validate_output(output: Any) -> dict[str, Any]:
    """The skill's output, checked again (it may have travelled through a form): always unverified, and
    the exact shape of ``research_programme.json``. Raises ``ValueError``."""
    if not isinstance(output, dict) or output.get("verification") != "unverified":
        raise ValueError("research output must be unverified (rule 11)")
    try:
        jsonschema.validate(output, load_schema("research_programme"))
    except jsonschema.ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path) or "the top level"
        raise ValueError(f"research output does not match the expected form at {where}") from None
    return output


@dataclass(frozen=True)
class Cited:
    """One researched value and where it came from, as shown in the preview."""

    key: str  # e.g. "name", "deadline:0", "requirement:1", "source:2"
    label: str  # e.g. "Name", "Deadline 1"
    value: str | None  # shown text; None when research found nothing
    source_url: str | None
    note: str | None
    filled: bool


def _show(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) if value else None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _deadline_text(d: dict[str, Any]) -> str:
    time_note = "" if d.get("time_known", True) else " (time to verify)"
    return f"{d['label']}: {d['due_local']} {d['tz']}{time_note}"


def _fee_text(fee: dict[str, Any]) -> str | None:
    if fee.get("amount") is None:
        return None
    return f"{fee.get('currency') or ''} {_show(fee['amount'])}".strip()


def cited_fields(output: dict[str, Any]) -> Iterator[Cited]:
    """Every value in a research output with its label and source, in preview order."""
    for key, label in SCALARS:
        item = output[key]
        value = item["value"]
        filled = value is not None and value != []
        yield Cited(key, label, _show(value), item["source_url"], item["note"], filled)
    fee = output["fee"]
    fee_text = _fee_text(fee)
    yield Cited("fee", "Fee", fee_text, fee["source_url"], fee["note"], fee_text is not None)
    for n, d in enumerate(output["deadlines"]):
        yield Cited(f"deadline:{n}", f"Deadline {n + 1}", _deadline_text(d), d["source_url"], None, True)
    for n, r in enumerate(output["requirements"]):
        text = r["label"] + (f" ({r['note']})" if r["note"] else "")
        yield Cited(f"requirement:{n}", f"Requirement {n + 1}", text, r["source_url"], None, True)
    for n, e in enumerate(output["essays"]):
        limit = f" ({e['word_limit']} words)" if e["word_limit"] else ""
        yield Cited(f"essay:{n}", f"Essay {n + 1}", e["title"] + limit, e["source_url"], e["prompt"], True)
    for n, c in enumerate(output["curriculum_courses"]):
        yield Cited(
            f"course:{n}", f"Curriculum course {n + 1}", c["name"], c["source_url"], c["description"], True
        )


def source_problem(
    label: str,
    url: str | None,
    filled: bool,
    domains: Sequence[str],
    shared: Sequence[str],
    field_name: str = "Source URL",
) -> str | None:
    """The message for a value whose source is missing or not official, else None."""
    if not url:
        return f"{label} · {field_name}: {MISSING_SOURCE}" if filled else None
    if is_official(url, domains, shared):
        return None
    shown = host_of(url) or url.strip()[:80]
    return f"{label} · {field_name}: {shown} is not an official source"


def _deadline_problems(n: int, d: dict[str, Any]) -> list[str]:
    try:
        Deadline(
            id="check",
            label=d["label"],
            due_local=d["due_local"],
            tz=d["tz"],
            time_known=d["time_known"],
            source_url=d["source_url"],
        )
    except ValidationError as exc:
        out = []
        for err in exc.errors():
            name = str(err["loc"][0]) if err["loc"] else ""
            what = DEADLINE_FIELD_LABELS.get(name, name or "Value")
            message = err["msg"].removeprefix("Value error, ")
            if name == "due_local":
                message = "must be a date and time like 2027-01-05T17:00"
            elif name == "source_url":
                message = "must be a web address starting with http:// or https://"
            out.append(f"Deadline {n + 1} · {what}: {message}")
        return out
    if not d["label"].strip():
        return [f"Deadline {n + 1} · Label: is required"]
    return []


@dataclass
class Proposal:
    """A checked research output: what the preview shows and whether Save is allowed."""

    output: dict[str, Any]
    domains: list[str]
    fields: list[Cited]
    refused: dict[str, str]  # Cited.key → message
    problems: list[str]
    not_found: list[str]  # labels of fields research found nothing for

    @property
    def can_save(self) -> bool:
        return not self.problems


def check_proposal(output: Any, domains: Sequence[str], shared: Sequence[str]) -> Proposal:
    """Run every check on a research output against the confirmed ``domains`` (preview and Save)."""
    output = validate_output(output)
    problems: list[str] = []
    refused: dict[str, str] = {}
    fields = list(cited_fields(output))
    not_found = [f.label for f in fields if not f.filled and f.key in dict(SCALARS) | {"fee": ""}]
    for f in fields:
        if (msg := source_problem(f.label, f.source_url, f.filled, domains, shared)) is not None:
            refused[f.key] = msg
            problems.append(msg)
    portal = output["portal_url"]["value"]
    if portal and (msg := source_problem("Portal", portal, True, domains, shared, "URL")) is not None:
        refused["portal_url"] = (
            refused.get("portal_url") or msg
        )  # the link itself is clicked (ai-output review)
        problems.append(msg)
    for n, s in enumerate(output["sources"]):
        if (msg := source_problem(f"Source {n + 1}", s["url"], True, domains, shared, "URL")) is not None:
            refused[f"source:{n}"] = msg
            problems.append(msg)
    usable = 0
    for n, d in enumerate(output["deadlines"]):
        own = _deadline_problems(n, d)
        problems += own
        if not own and f"deadline:{n}" not in refused:
            usable += 1
    labels = dict(SCALARS)
    for key in REQUIRED_SCALARS:
        if output[key]["value"] in (None, ""):
            problems.append(f"{labels[key]}: no official source gave it, and a programme needs it")
    if usable == 0:
        problems.append(SAVE_NEEDS_DEADLINE)
    return Proposal(output, list(domains), fields, refused, problems, not_found)


# ---- building a new programme --------------------------------------------------------------------------


def _sources(output: dict[str, Any], cited: Sequence[str], today: date) -> list[Source]:
    titles = {s["url"]: s["title"] for s in output["sources"]}
    urls = list(dict.fromkeys([*titles, *cited]))
    return [Source(url=u, title=titles.get(u) or host_of(u) or u, checked=today) for u in urls]


def build_programme(
    output: Any,
    domains: Sequence[str],
    *,
    taken_ids: set[str],
    today: date,
    shared: Sequence[str] = (),
) -> Programme:
    """A new programme from a checked research output: always unverified ("Researched, not checked"),
    every deadline unverified, ``sources`` = the cited pages checked today, and the note "No official
    source found" where letters or the fee stayed null. Raises ``ValueError`` if any check fails."""
    proposal = check_proposal(output, domains, shared)
    if proposal.problems:
        raise ValueError("; ".join(proposal.problems))
    out = proposal.output

    def value(key: str) -> Any:
        return out[key]["value"]

    pid = unique_slug(slugify(value("short_name"), "programme"), set(taken_ids))
    deadline_ids: set[str] = set()
    deadlines = [
        Deadline(
            id=unique_slug(slugify(d["label"], "deadline"), deadline_ids),
            label=d["label"],
            due_local=d["due_local"],
            tz=d["tz"],
            time_known=d["time_known"],
            verification=Verification.UNVERIFIED,
            source_url=d["source_url"],
        )
        for d in out["deadlines"]
    ]
    req_ids: set[str] = set()
    essay_ids: set[str] = set()
    letters_required = value("letters_required")
    letters_note = value("letters_note") or (NO_OFFICIAL_SOURCE if letters_required is None else None)
    fee = out["fee"]
    policy = value("ai_policy")
    cited = [f.source_url for f in proposal.fields if f.filled and f.source_url]
    return Programme(
        id=pid,
        name=value("name"),
        short_name=value("short_name"),
        school=value("school"),
        unit=value("unit"),
        official_domains=list(domains),
        aliases=list(value("aliases") or []),
        verification=Verification.UNVERIFIED,
        verification_note=RESEARCHED_NOTE,
        portal_url=value("portal_url"),
        sources=_sources(out, cited, today),
        deadlines=deadlines,
        letters_required=letters_required,
        letters_note=letters_note,
        requirements=[
            Requirement(
                id=unique_slug(slugify(r["label"], "requirement"), req_ids), label=r["label"], note=r["note"]
            )
            for r in out["requirements"]
        ],
        essays=[
            EssayPrompt(
                id=unique_slug(slugify(e["title"], "essay"), essay_ids),
                title=e["title"],
                prompt=e["prompt"],
                word_limit=e["word_limit"],
            )
            for e in out["essays"]
        ],
        curriculum=Curriculum(
            summary=value("curriculum_summary"),
            courses=[Course(name=c["name"], description=c["description"]) for c in out["curriculum_courses"]],
        ),
        ai_policy=AiPolicy(text=policy, source_url=out["ai_policy"]["source_url"] if policy else None),
        fee=Fee(
            amount=fee["amount"],
            currency=fee["currency"] or "USD",
            note=fee["note"] or (NO_OFFICIAL_SOURCE if fee["amount"] is None else None),
        ),
    )


def apply_deadline_edits(
    output: dict[str, Any], rows: list[dict[str, str]], assumed_time: str
) -> dict[str, Any]:
    """The preview's deadline rows, as the user left them, in place of the researched deadlines.

    A row left entirely blank is dropped. A row without a time gets ``assumed_time``
    (``config.time.assumed_deadline_time``) and is marked "time to verify" (``time_known`` false).
    """
    deadlines = []
    for row in rows:
        if not any(v.strip() for k, v in row.items() if k != "time_known"):
            continue
        time = row.get("time", "").strip()
        deadlines.append(
            {
                "id": None,
                "label": row.get("label", "").strip(),
                "due_local": f"{row.get('date', '').strip()}T{time or assumed_time}",
                "tz": row.get("tz", "").strip(),
                "time_known": bool(time) and bool(row.get("time_known")),
                "source_url": row.get("source_url", "").strip(),
            }
        )
    return {**output, "deadlines": deadlines}


# ---- re-research: comparison and apply ------------------------------------------------------------------


@dataclass
class Change:
    """One row of the re-research comparison."""

    id: str  # e.g. "letters_required", "deadline:main", "requirement:cv", "official_domains"
    label: str
    kind: str  # new | changed | unchanged | kept
    old: str | None
    new: str | None
    source_url: str | None
    refused: str | None = None  # a new/changed row whose source is missing or not official
    proposed: Any = field(default=None, repr=False)  # the researched item, for apply
    target: int | None = None  # index of the stored item it changes (lists)

    @property
    def acceptable(self) -> bool:
        return self.kind in {"new", "changed"} and self.refused is None


def _kind(old: Any, new: Any) -> str:
    if new is None or new == []:
        return "unchanged" if old is None or old == [] else "kept"
    if old is None or old == []:
        return "new"
    return "unchanged" if old == new else "changed"


def _scalar_current(p: Programme, key: str) -> Any:
    return {
        "aliases": p.aliases or None,
        "curriculum_summary": p.curriculum.summary,
        "ai_policy": p.ai_policy.text,
    }.get(key, getattr(p, key, None))


def _match(
    items: list[Any], key_of_item: Any, wanted_id: str | None, wanted_name: str, used: set[int]
) -> int | None:
    for i, item in enumerate(items):
        if i not in used and wanted_id and getattr(item, "id", None) == wanted_id:
            return i
    for i, item in enumerate(items):
        if i not in used and key_of_item(item).casefold() == wanted_name.casefold():
            return i
    return None


def compare(current: Programme, output: Any, domains: Sequence[str], shared: Sequence[str]) -> list[Change]:
    """Field-by-field comparison of a stored programme with a research output (docs/04 §7.1)."""
    out = validate_output(output)
    changes: list[Change] = []

    def add(change: Change, label_for_source: str | None = None) -> None:
        if change.kind in {"new", "changed"}:
            change.refused = source_problem(
                label_for_source or change.label, change.source_url, True, domains, shared
            )
        changes.append(change)

    if list(domains) != list(current.official_domains):
        kind = "new" if not current.official_domains else "changed"
        changes.append(
            Change(
                "official_domains",
                "Official domains",
                kind,
                _show(current.official_domains),
                _show(list(domains)),
                None,
                proposed=list(domains),
            )
        )
    else:
        shown = _show(list(domains))
        changes.append(Change("official_domains", "Official domains", "unchanged", shown, shown, None))
    for key, label in SCALARS:
        item = out[key]
        old, new = _scalar_current(current, key), item["value"]
        change = Change(
            key, label, _kind(old, new), _show(old), _show(new), item["source_url"], proposed=item
        )
        add(change)
        if key == "portal_url" and change.kind in {"new", "changed"} and not change.refused:
            # the portal link itself is clicked, so its host must be official too (#65, #67)
            change.refused = source_problem("Portal", new, True, domains, shared, "URL")
    fee = out["fee"]
    old_fee = (current.fee.amount, current.fee.currency) if current.fee.amount is not None else None
    new_fee = (fee["amount"], fee["currency"]) if fee["amount"] is not None else None
    old_text = _fee_text({"amount": current.fee.amount, "currency": current.fee.currency})
    add(
        Change(
            "fee", "Fee", _kind(old_fee, new_fee), old_text, _fee_text(fee), fee["source_url"], proposed=fee
        )
    )

    def stored_deadline(d: Deadline) -> dict[str, Any]:
        return {
            "label": d.label,
            "due_local": d.due_local.strftime("%Y-%m-%dT%H:%M"),
            "tz": d.tz,
            "time_known": d.time_known,
            "source_url": d.source_url,
        }

    used: set[int] = set()
    taken = {d.id for d in current.deadlines}
    for n, d in enumerate(out["deadlines"]):
        i = _match(current.deadlines, lambda x: x.label, d["id"], d["label"], used)
        label = f"Deadline {n + 1}"
        if i is None:
            new_id = unique_slug(slugify(d["label"], "deadline"), taken)
            add(
                Change(
                    f"deadline:{new_id}", label, "new", None, _deadline_text(d), d["source_url"], proposed=d
                )
            )
            continue
        used.add(i)
        stored = stored_deadline(current.deadlines[i])
        proposed = {k: d[k] for k in stored}
        kind = "unchanged" if stored == proposed else "changed"
        add(
            Change(
                f"deadline:{current.deadlines[i].id}",
                label,
                kind,
                _deadline_text(stored),
                _deadline_text(d),
                d["source_url"],
                proposed=d,
                target=i,
            )
        )
    for i, d in enumerate(current.deadlines):
        if i not in used:
            text = _deadline_text(stored_deadline(d))
            changes.append(
                Change(f"deadline:{d.id}", f"Stored deadline {d.label}", "kept", text, None, d.source_url)
            )
    _compare_items(
        changes,
        add,
        current.requirements,
        out["requirements"],
        "requirement",
        "Requirement",
        name=lambda x: x.label,
        out_name=lambda r: r["label"],
        # labels only: since #66 research never sees the owner's notes, so it can't change them (#67)
        stored=lambda x: (x.label,),
        new=lambda r: (r["label"],),
        text=lambda v: v[0],
    )
    _compare_items(
        changes,
        add,
        current.essays,
        out["essays"],
        "essay",
        "Essay",
        name=lambda x: x.title,
        out_name=lambda e: e["title"],
        stored=lambda x: (x.title, x.prompt, x.word_limit),
        new=lambda e: (e["title"], e["prompt"], e["word_limit"]),
        text=lambda v: v[0] + (f" ({v[2]} words)" if v[2] else ""),
    )
    _compare_items(
        changes,
        add,
        current.curriculum.courses,
        out["curriculum_courses"],
        "course",
        "Curriculum course",
        name=lambda x: x.name,
        out_name=lambda c: c["name"],
        stored=lambda x: (x.name, x.description),
        new=lambda c: (c["name"], c["description"]),
        text=lambda v: v[0],
        ids=False,
    )
    return changes


def _compare_items(
    changes, add, items, proposed_items, prefix, label, *, name, out_name, stored, new, text, ids=True
) -> None:
    """Rows for a list matched by id (if the list has ids), else by name, ignoring case."""
    used: set[int] = set()
    taken = {item.id for item in items} if ids else {slugify(name(item), prefix) for item in items}
    for n, entry in enumerate(proposed_items):
        i = _match(items, name, entry.get("id") if ids else None, out_name(entry), used)
        row_label = f"{label} {n + 1}"
        if i is None:
            new_id = unique_slug(slugify(out_name(entry), prefix), taken)
            add(
                Change(
                    f"{prefix}:{new_id}",
                    row_label,
                    "new",
                    None,
                    text(new(entry)),
                    entry["source_url"],
                    proposed=entry,
                )
            )
            continue
        used.add(i)
        item_id = items[i].id if ids else slugify(name(items[i]), prefix)
        old_v, new_v = stored(items[i]), new(entry)
        kind = "unchanged" if old_v == new_v else "changed"
        add(
            Change(
                f"{prefix}:{item_id}",
                row_label,
                kind,
                text(old_v),
                text(new_v),
                entry["source_url"],
                proposed=entry,
                target=i,
            )
        )
    for i, item in enumerate(items):
        if i not in used:
            item_id = item.id if ids else slugify(name(item), prefix)
            changes.append(
                Change(
                    f"{prefix}:{item_id}", f"Stored {label.lower()}", "kept", text(stored(item)), None, None
                )
            )


def apply(
    current: Programme,
    output: Any,
    domains: Sequence[str],
    accepted: set[str],
    today: date,
    shared: Sequence[str] = (),
) -> Programme | None:
    """The stored programme with the accepted changes, or None when nothing was accepted (nothing to write).

    Raises ``ValueError`` for an id that isn't a new/changed row or whose source is refused. The result is
    a changed copy of the loaded model, so the store's merge keeps the file's comments and shape (#29b).
    """
    changes = {c.id: c for c in compare(current, output, domains, shared)}
    for change_id in sorted(accepted):
        change = changes.get(change_id)
        if change is None or change.kind not in {"new", "changed"}:
            raise ValueError(f"{change_id} cannot be accepted: it has no new or changed value")
        if change.refused:
            raise ValueError(change.refused)
    if not accepted:
        return None
    out = validate_output(output)
    p = current.model_copy(deep=True)
    researched = False
    cited: list[str] = []
    for change_id in [c for c in changes if c in accepted]:  # comparison order
        change = changes[change_id]
        prefix, _, item_id = change_id.partition(":")
        if change.source_url:
            cited.append(change.source_url)
        if change_id == "official_domains":
            p.official_domains = list(change.proposed)
        elif prefix == "deadline":
            _apply_deadline(p, change, item_id)
        else:
            researched = True
            _apply_field(p, change, prefix, item_id)
    if researched:
        p.verification = Verification.UNVERIFIED
        p.verification_note = RESEARCHED_NOTE
    known = {s.url for s in p.sources}
    titles = {s["url"]: s["title"] for s in out["sources"]}
    new_sources = [u for u in dict.fromkeys(cited) if u not in known]
    if new_sources:
        p.sources = [
            *p.sources,
            *(Source(url=u, title=titles.get(u) or host_of(u) or u, checked=today) for u in new_sources),
        ]
    Programme.model_validate(p.model_dump(mode="python"))  # the whole result must still be valid
    return p


def _apply_deadline(p: Programme, change: Change, item_id: str) -> None:
    d = change.proposed
    if change.target is None:
        p.deadlines = [
            *p.deadlines,
            Deadline(
                id=item_id,
                label=d["label"],
                due_local=d["due_local"],
                tz=d["tz"],
                time_known=d["time_known"],
                verification=Verification.UNVERIFIED,
                source_url=d["source_url"],
            ),
        ]
        return
    stored = p.deadlines[change.target]
    new_due = _LOCAL.validate_python(d["due_local"])
    reset = new_due != stored.due_local or d["tz"] != stored.tz or d["source_url"] != stored.source_url
    # research saying a "time to verify" deadline's time is now known is a new claim, not a checked one
    reset = reset or d["time_known"] != stored.time_known  # a researched "time known" claim, either way
    if d["label"] != stored.label:
        stored.label = d["label"]
    if new_due != stored.due_local:
        stored.due_local = new_due
    if d["tz"] != stored.tz:
        stored.tz = d["tz"]
    if d["time_known"] != stored.time_known:
        stored.time_known = d["time_known"]
    if d["source_url"] != stored.source_url:
        stored.source_url = d["source_url"]
    if reset:  # date, time, zone or source changed: not checked any more (DECISIONS #21, #30)
        stored.verification = Verification.UNVERIFIED


def _apply_field(p: Programme, change: Change, prefix: str, item_id: str) -> None:
    item = change.proposed
    if prefix == "requirement":
        if change.target is None:
            p.requirements = [
                *p.requirements,
                Requirement(id=item_id, label=item["label"], note=item["note"]),
            ]
        else:
            stored = p.requirements[change.target]
            stored.label = item["label"]  # the owner's note stays (#67)
    elif prefix == "essay":
        if change.target is None:
            p.essays = [
                *p.essays,
                EssayPrompt(
                    id=item_id, title=item["title"], prompt=item["prompt"], word_limit=item["word_limit"]
                ),
            ]
        else:
            stored = p.essays[change.target]
            stored.title, stored.prompt, stored.word_limit = item["title"], item["prompt"], item["word_limit"]
    elif prefix == "course":
        if change.target is None:
            p.curriculum.courses = [
                *p.curriculum.courses,
                Course(name=item["name"], description=item["description"]),
            ]
        else:
            stored = p.curriculum.courses[change.target]
            stored.name, stored.description = item["name"], item["description"]
    elif prefix == "fee":
        p.fee.amount, p.fee.currency = item["amount"], item["currency"] or p.fee.currency
        if item["note"]:
            p.fee.note = item["note"]
    elif prefix == "ai_policy":
        p.ai_policy.text, p.ai_policy.source_url = item["value"], item["source_url"]
    elif prefix == "curriculum_summary":
        p.curriculum.summary = item["value"]
    elif prefix == "aliases":
        p.aliases = list(item["value"])
    else:
        setattr(p, prefix, item["value"])
