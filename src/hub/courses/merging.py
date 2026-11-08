"""Merging duplicate courses (follow-up before Phase 3; DECISIONS #95; docs/01 §10, docs/03 §4.8).

Two stored courses are the same course when both have a code and the codes match (case, spaces and
punctuation ignored), or, when not both have one, when their names match (case and punctuation ignored); in
both cases the institutions must be equal or one empty. On /courses/merge each group is merged into one kept
course: a field filled in one copy only is taken from it; where copies disagree the owner picks (a case-only
difference is pre-set to the form in that course's sources/ text), and a picked field counts as owner-edited.
At import, a row matching a stored course is offered "Merge into <course>": only the course's empty fields are
filled, conflicts are shown and the stored value is kept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from hub.courses.editing import FIELDS
from hub.models.course import TranscriptCourse

_PUNCT = re.compile(r"[^\w]+")


def _name_key(name: str | None) -> str:
    return " ".join(_PUNCT.sub(" ", (name or "").casefold()).split())


def _code_key(code: str | None) -> str:
    return _PUNCT.sub("", (code or "").casefold()).replace("_", "")


def _institution_key(institution: str | None) -> str:
    return " ".join((institution or "").casefold().split())


def same_course(a: Any, b: Any) -> bool:
    """True when ``a`` and ``b`` (courses or import rows) are the same course (see the module docstring)."""
    get = (lambda x, k: getattr(x, k, None)) if not isinstance(a, dict) else (lambda x, k: x.get(k))
    get_b = (lambda x, k: getattr(x, k, None)) if not isinstance(b, dict) else (lambda x, k: x.get(k))
    ia, ib = _institution_key(get(a, "institution")), _institution_key(get_b(b, "institution"))
    if ia and ib and ia != ib:
        return False
    ca, cb = _code_key(get(a, "code")), _code_key(get_b(b, "code"))
    if ca and cb:
        return ca == cb
    na, nb = _name_key(get(a, "name")), _name_key(get_b(b, "name"))
    return bool(na) and na == nb


def groups(courses: list[TranscriptCourse]) -> list[list[TranscriptCourse]]:
    """The groups of two or more active courses that are the same course, in file order."""
    active = [c for c in courses if not c.deleted]
    parent = list(range(len(active)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(active)):
        for j in range(i + 1, len(active)):
            if same_course(active[i], active[j]):
                parent[root(j)] = root(i)
    by_root: dict[int, list[TranscriptCourse]] = {}
    for i, course in enumerate(active):
        by_root.setdefault(root(i), []).append(course)
    return [g for g in by_root.values() if len(g) > 1]


def _value(course: TranscriptCourse, name: str) -> Any:
    value = getattr(course, name)
    return tuple(value) if isinstance(value, list) else value


def _filled(value: Any) -> bool:
    return value not in (None, "", ())


def _case_only(values: list[Any]) -> bool:
    return all(isinstance(v, str) for v in values) and len({v.casefold() for v in values}) == 1


@dataclass
class FieldChoice:
    """One field of a group: the copies that fill it, whether they disagree, and the pre-set pick."""

    name: str
    options: list[tuple[TranscriptCourse, Any]]  # (copy, its value), one per distinct value
    preset: str | None = None  # the copy id pre-selected (case-only differences), else None

    @property
    def conflict(self) -> bool:
        return len(self.options) > 1


@dataclass
class Group:
    copies: list[TranscriptCourse]
    keep: str  # the copy pre-set to be kept
    fields: list[FieldChoice] = field(default_factory=list)


def plan(
    copies: list[TranscriptCourse], link_counts: dict[str, int], source_text: Any, keep: str | None = None
) -> Group:
    """The preview of merging ``copies``. ``link_counts``: professors linking each copy (the copy most link is
    pre-set to be kept, ties the earliest). ``source_text(course)``: the text of the course's sources/ file
    (or ""), where a case-only difference finds the transcript's form."""
    if keep is None:
        keep = max(copies, key=lambda c: (link_counts.get(c.id, 0), -copies.index(c))).id
    kept = next(c for c in copies if c.id == keep)
    choices = []
    for name in FIELDS:
        seen: list[tuple[TranscriptCourse, Any]] = []
        for copy in copies:
            value = _value(copy, name)
            if _filled(value) and all(value != v for _, v in seen):
                seen.append((copy, value))
        choice = FieldChoice(name, seen)
        if choice.conflict and _case_only([v for _, v in seen]):
            written = next(
                (c.id for c, v in seen if re.search(rf"(?<![\w]){re.escape(v)}(?![\w])", source_text(c))),
                None,
            )
            choice.preset = written or next((c.id for c, _ in seen if c.id == kept.id), seen[0][0].id)
        choices.append(choice)
    return Group(copies, keep, choices)


class MergeRefused(Exception):
    """The submitted merge can't be saved; the message says what to do. Nothing was written."""


def merged(group: Group, picks: dict[str, str]) -> TranscriptCourse:
    """The kept course with every field merged: a field one copy fills comes from it; a conflict takes the
    picked copy's value (``picks``: field → copy id). Picked fields, and fields taken from a copy that had
    them as owner-edited, are recorded in ``edited`` (DECISIONS #95)."""
    kept = next(c for c in group.copies if c.id == group.keep)
    by_id = {c.id: c for c in group.copies}
    update: dict[str, Any] = {}
    edited = set(kept.edited)
    institution_from = kept
    for choice in group.fields:
        if not choice.options:
            continue
        if choice.conflict:
            pick = picks.get(choice.name)
            if pick not in {c.id for c, _ in choice.options}:
                raise MergeRefused(f"Pick a value for {choice.name.capitalize()}. Nothing was merged.")
            source = by_id[pick]
            edited.add(choice.name)
        else:
            source = choice.options[0][0]
        value = getattr(source, choice.name)
        update[choice.name] = list(value) if isinstance(value, list | tuple) else value
        if choice.name in source.edited:
            edited.add(choice.name)
        if choice.name == "institution":
            institution_from = source
    update["edited"] = [n for n in FIELDS if n in edited]
    update["institution_entered"] = institution_from.institution_entered
    return kept.model_copy(update=update)


def fill_empty(course: TranscriptCourse, row: dict[str, Any]) -> tuple[TranscriptCourse, list[str]]:
    """Import-time "Merge into": the stored course with only its empty fields filled from the import row, and
    the fields that differ (the stored value is kept). Filled fields come from a transcript, so they are not
    owner-edited."""
    update: dict[str, Any] = {}
    differs: list[str] = []
    for name in FIELDS:
        stored = _value(course, name)
        new = row.get(name)
        new = tuple(new) if isinstance(new, list) else new
        if name == "credits" and new not in (None, ""):
            new = float(new)
        if not _filled(new):
            continue
        if not _filled(stored):
            update[name] = list(new) if isinstance(new, tuple) else new
        elif stored != new:
            differs.append(name)
    return (course.model_copy(update=update) if update else course), differs
