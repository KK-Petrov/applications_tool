"""Add standard steps and documents to a programme (DECISIONS #97; docs/03 §4.2).

A deterministic proposal, no AI call. **Documents** come from the programme's stored requirements through the
templates in ``config.standard_items.documents``: a requirement whose id or label has one of a template's
``requirement_words`` (whole words, any case) yields that document, once however many requirements name it
(transcripts → official transcript; GMAT / GRE → their score report; an English test → its score report).
**Steps** come from the fixed template ``config.standard_items.steps`` in its order, each linked to every
requirement it names. A template whose ``exists_words`` already stand in the label of one of the programme's
documents (or steps) is "already there" and never added again, so nothing is duplicated or overwritten.

Phase 3's ``documents_due`` reminder fires only for programmes that have documents (docs/02 §5); this is how
a researched programme gets them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from hub.models.programme import Document, DocumentStatus, Programme, Requirement, Step
from hub.programme_form import unique_slug

if TYPE_CHECKING:
    from hub.config import StandardItem, StandardItemsConfig

Kind = Literal["step", "document"]
_WORD = re.compile(r"[^\W_]+")


class UnknownItem(ValueError):
    """A submitted key names no standard item of this programme; nothing is written."""


@dataclass(frozen=True)
class Proposal:
    """One row of the preview."""

    key: str  # "step:fee", "document:gmat": the form value
    kind: Kind
    label: str
    requirements: list[Requirement] = field(
        default_factory=list
    )  # linked (steps) or derived from (documents)
    existing: str | None = None  # the label of the item that is already there; None → new


def words(text: str) -> set[str]:
    """The whole words of ``text``, case-folded ("English-language" → english, language)."""
    return set(_WORD.findall(text.casefold()))


def _named(item: StandardItem, requirement: Requirement) -> bool:
    return bool(set(item.requirement_words) & (words(requirement.id) | words(requirement.label)))


def _already(item: StandardItem, labels: list[str]) -> str | None:
    return next((label for label in labels if set(item.exists_words) & words(label)), None)


def propose(p: Programme, cfg: StandardItemsConfig) -> list[Proposal]:
    """Every standard step (template order), then every document its requirements call for."""
    step_labels = [s.label for s in sorted(p.steps, key=lambda s: s.order)]
    doc_labels = [d.label for d in p.documents]
    out: list[Proposal] = []
    for item in cfg.steps:
        linked = [r for r in p.requirements if _named(item, r)]
        out.append(Proposal(f"step:{item.key}", "step", item.label, linked, _already(item, step_labels)))
    for item in cfg.documents:
        named = [r for r in p.requirements if _named(item, r)]
        if named:
            out.append(
                Proposal(f"document:{item.key}", "document", item.label, named, _already(item, doc_labels))
            )
    return out


def apply(
    p: Programme, keys: set[str], cfg: StandardItemsConfig
) -> tuple[Programme, list[Step], list[Document]]:
    """A copy of ``p`` with the chosen items that are still new added (steps after the highest order,
    documents with fresh ids); the existing items are untouched. Raises ``UnknownItem`` for a key that is no
    standard item of this programme. Returns the copy and the steps and documents added."""
    proposals = {x.key: x for x in propose(p, cfg)}
    unknown = sorted(keys - set(proposals))
    if unknown:
        raise UnknownItem(f"{unknown[0]} is not a standard item of this programme")
    out = p.model_copy(deep=True)
    order = max((s.order for s in out.steps), default=0)
    taken = {d.id for d in out.documents}
    steps: list[Step] = []
    documents: list[Document] = []
    by_key = {f"step:{i.key}": i for i in cfg.steps} | {f"document:{i.key}": i for i in cfg.documents}
    for key, proposal in proposals.items():  # template order, whatever order the form sent
        if key not in keys or proposal.existing is not None:
            continue
        if proposal.kind == "step":
            order += 1
            data = {"order": order, "label": proposal.label, "note": None, "done": False}
            if proposal.requirements:  # an unlinked step gets no empty key
                data["requirement_ids"] = [r.id for r in proposal.requirements]
            steps.append(Step(**data))
        else:
            doc_id = unique_slug(by_key[key].key, taken)
            documents.append(
                Document(
                    id=doc_id,
                    label=proposal.label,
                    method=None,  # how it is sent is the school's rule: never guessed
                    status=DocumentStatus.NOT_REQUESTED,
                    updated=None,
                    note=None,
                )
            )
    if steps:
        out.steps = [*out.steps, *steps]
    if documents:
        out.documents = [*out.documents, *documents]
    return out, steps, documents


def dropped_links(old: Programme, new: Programme) -> list[str]:
    """What Edit programme says when a step lost its link because the requirement was removed (user addition
    to Q2, DECISIONS #97): "<step> is no longer linked to <requirement>", one per lost link."""
    labels = {r.id: r.label for r in old.requirements}
    kept = {s.order: set(s.requirement_ids) for s in new.steps}
    out = []
    for step in old.steps:
        if step.order not in kept:
            continue  # the step itself was removed
        for rid in step.requirement_ids:
            if rid not in kept[step.order]:
                new_label = next((s.label for s in new.steps if s.order == step.order), step.label)
                out.append(f"{new_label} is no longer linked to {labels.get(rid, rid)}")
    return out
