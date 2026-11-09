"""Update emails from a list of names and emails (Phase 3 pre-build, user request; DECISIONS #104).

The owner pastes or uploads two columns, name and email, typically copied from a faculty page or a
spreadsheet. Each row is matched to an existing professor by the names rule (docs/02 §9: surname plus
initials, case- and diacritic-insensitive; a name without initials matches on the surname only, which is
offered, never taken silently). The preview says, per row, what Save would do:

- **fill**: the professor has no email; ticked when the match is exact, unticked for a surname-only match;
- **conflict**: the professor has a different email; both are shown and the owner picks Keep or Use;
- every other row is listed with its reason and never used: already has this email, no match, more than one
  match, an email another professor uses (DECISIONS #41 f), the same professor or the same email on two rows,
  an invalid email or a row without exactly two columns.

Nothing is written here; the route writes the chosen rows through the store.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import TypeAdapter, ValidationError

from hub.courses.lecturers import parsed_professor
from hub.models.common import Email
from hub.models.professor import Professor
from hub.rules import names

if TYPE_CHECKING:
    from hub.config import NamesConfig

# A first row whose two cells are these words is a header (case-insensitive); any other first row is data.
NAME_HEADERS = frozenset({"name", "naam", "professor", "lecturer", "docent"})
EMAIL_HEADERS = frozenset(
    {"email", "e-mail", "mail", "email address", "e-mail address", "emailadres", "e-mailadres"}
)
_EMAIL = TypeAdapter(Email)


class Kind(StrEnum):
    FILL = "fill"  # the professor has no email
    CONFLICT = "conflict"  # the professor has a different email: Keep or Use, nothing pre-selected
    SAME = "same"  # the professor already has this email
    UNMATCHED = "unmatched"
    AMBIGUOUS = "ambiguous"
    TAKEN = "taken"  # another professor uses this email
    REPEATED = "repeated"  # the same professor or the same email on another row
    INVALID = "invalid"  # not two columns, no name, or not an email address


ACTIONABLE = (Kind.FILL, Kind.CONFLICT)


@dataclass(frozen=True)
class InputRow:
    """One row of the input as written. ``number`` is its line among the non-empty rows (header = 1)."""

    number: int
    name: str
    email: str
    error: str | None = None


@dataclass
class Proposal:
    """What Save would do with one row."""

    row: InputRow
    kind: Kind
    professor: Professor | None = None
    surname_only: bool = False  # matched on the surname only: offered, never ticked
    candidates: list[Professor] = field(default_factory=list)  # for an ambiguous row
    reason: str = ""

    @property
    def actionable(self) -> bool:
        return self.kind in ACTIONABLE

    @property
    def ticked(self) -> bool:
        """Pre-ticked in the preview: an exact match filling an empty email."""
        return self.kind == Kind.FILL and not self.surname_only


@dataclass
class Preview:
    proposals: list[Proposal]
    header: bool = False
    error: str | None = None

    @property
    def actionable(self) -> list[Proposal]:
        return [p for p in self.proposals if p.actionable]

    @property
    def not_used(self) -> list[Proposal]:
        return [p for p in self.proposals if not p.actionable]


def _delimiter(line: str) -> str:
    """A tab when pasted from a spreadsheet, else ";" (Dutch Excel), else ","."""
    if "\t" in line:
        return "\t"
    return ";" if ";" in line else ","


def _is_header(cells: list[str]) -> bool:
    return len(cells) == 2 and names.fold(cells[0]) in NAME_HEADERS and names.fold(cells[1]) in EMAIL_HEADERS


def read_rows(text: str) -> tuple[list[InputRow], bool]:
    """The rows of pasted or uploaded text, and whether a header row was skipped.

    Blank lines are skipped and not counted. A row needs exactly two columns, a name and a valid email;
    otherwise it carries its error and is listed, never used.
    """
    text = text.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line for line in text.split("\n") if line.strip()]
    if not lines:
        return [], False
    reader = csv.reader(io.StringIO("\n".join(lines), newline=""), delimiter=_delimiter(lines[0]))
    rows: list[InputRow] = []
    header = False
    for number, raw in enumerate(reader, start=1):
        cells = [c.strip() for c in raw]
        while len(cells) > 2 and not cells[-1]:
            cells.pop()  # trailing empty columns from a spreadsheet
        if number == 1 and _is_header(cells):
            header = True
            continue
        if len(cells) != 2:
            shown = " · ".join(c for c in cells if c)
            rows.append(InputRow(number, shown, "", "needs two columns: a name and an email"))
            continue
        name, email = cells
        error = None
        if not name:
            error = "has no name"
        else:
            try:
                _EMAIL.validate_python(email)
            except ValidationError:
                error = "must have an email address, e.g. name@example.org"
        rows.append(InputRow(number, name, email, error))
    return rows, header


def _match(row: InputRow, parsed: list[tuple[Professor, names.ParsedName]], cfg: NamesConfig) -> Proposal:
    """The professor a row names: one exact match, else one surname-only match; never a guess."""
    wanted = names.parse(row.name, cfg)
    found = [(p, kind) for p, pn in parsed if (kind := names.match(wanted, pn))]
    exact = [p for p, kind in found if kind == "exact"]
    surname = [p for p, kind in found if kind == "surname"]
    if len(exact) == 1:
        return Proposal(row, Kind.FILL, exact[0])
    many = exact or surname
    if len(many) > 1:
        listed = ", ".join(p.name for p in many)
        return Proposal(
            row,
            Kind.AMBIGUOUS,
            candidates=many,
            reason=f"Matches more than one professor ({listed}); nothing is guessed. Add the initials",
        )
    if surname:
        return Proposal(row, Kind.FILL, surname[0], surname_only=True)
    return Proposal(row, Kind.UNMATCHED, reason="No professor matches this name")


def _numbers(rows: list[int]) -> str:
    return " and ".join(str(n) for n in rows) if len(rows) <= 2 else ", ".join(str(n) for n in rows)


def propose(text: str, professors: list[Professor], cfg: NamesConfig) -> Preview:
    """The preview for ``text``: one proposal per row, in input order."""
    rows, header = read_rows(text)
    if not rows:
        return Preview([], header, "The text has no rows with a name and an email.")
    parsed = [(p, parsed_professor(p, cfg)) for p in professors]
    proposals: list[Proposal] = []
    for row in rows:
        if row.error:
            proposals.append(Proposal(row, Kind.INVALID, reason=row.error[:1].upper() + row.error[1:]))
        else:
            proposals.append(_match(row, parsed, cfg))
    matched = [p for p in proposals if p.professor is not None]
    for proposal in matched:  # against what is stored first, so a row confirming an email is never a repeat
        if proposal.kind != Kind.FILL:
            continue
        prof = proposal.professor
        assert prof is not None
        email = proposal.row.email
        twin = next(
            (o for o in professors if o.id != prof.id and o.email and o.email.casefold() == email.casefold()),
            None,
        )
        if twin is not None:
            proposal.kind, proposal.reason = Kind.TAKEN, f"This email is already used by {twin.name}"
        elif prof.email and prof.email.casefold() == email.casefold():
            proposal.kind, proposal.reason = Kind.SAME, f"{prof.name} already has this email"
        elif prof.email:
            proposal.kind = Kind.CONFLICT
    _repeats([p for p in matched if p.actionable])  # then within the list, among rows that change something
    return Preview(proposals, header)


def _repeats(matched: list[Proposal]) -> None:
    """Refuse rows that give one professor two emails, or one email to two professors; an identical row
    again (same professor, same email) is "same as row N"."""
    first_for: dict[tuple[str, str], int] = {}
    for proposal in matched:  # identical rows: keep the first
        assert proposal.professor is not None
        key = (proposal.professor.id, proposal.row.email.casefold())
        if key in first_for:
            proposal.kind, proposal.reason = Kind.REPEATED, f"The same as row {first_for[key]}"
        else:
            first_for[key] = proposal.row.number
    live = [p for p in matched if p.kind != Kind.REPEATED]
    by_prof: dict[str, list[Proposal]] = {}
    by_email: dict[str, list[Proposal]] = {}
    for proposal in live:
        assert proposal.professor is not None
        by_prof.setdefault(proposal.professor.id, []).append(proposal)
        by_email.setdefault(proposal.row.email.casefold(), []).append(proposal)
    for group in by_prof.values():
        if len(group) > 1:
            nums = _numbers([p.row.number for p in group])
            who = group[0].professor.name if group[0].professor else ""
            for p in group:
                p.kind, p.reason = (
                    Kind.REPEATED,
                    f"Rows {nums} give different emails for {who}; neither is used",
                )
    for group in by_email.values():
        if len(group) > 1:
            nums = _numbers([p.row.number for p in group])
            for p in group:
                if p.kind != Kind.REPEATED:
                    p.kind = Kind.REPEATED
                    p.reason = f"Rows {nums} give the same email to different professors; neither is used"
