"""Names (docs/02 §9; DECISIONS #44): title tokens, initials, Dutch particles, the salutation and matching.

A name is parsed as written, with ``config.names``:
1. **Title tokens** (prof., dr., ir., drs., mr. …; case-insensitive, with or without a full stop, also joined
   as "Prof.dr.ir.") are stripped; the highest-ranked one (``names.title_precedence``) becomes the title,
   else "Professor".
2. **Initials** are one letter with a full stop, possibly joined ("J.A." = J, A). Without any, they are taken
   from the first names ("Jan de Vries" → J), so the matching key is the same either way.
3. **Particles** (van, den, der, de, ter, 't, in, op, von …) belong to the surname: a run of them before the
   last word starts it ("Prof. dr. A.B. van den Berg" → "van den Berg").
4. **Salutation**: the surname keeps its particle in lower case after a first name or initials; standing
   alone ("Dear Professor Van den Berg,") the first letter is capitalised.
5. **Matching**: surname plus initials, case- and diacritic-insensitive. A name without initials matches on
   the surname only; that is offered for linking, never merged silently. With first names written on both
   sides they must agree too, else the match is surname-only ("Lise" ≠ "Lars"; DECISIONS #105).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hub.config import NamesConfig

DEFAULT_TITLE = "Professor"
# Letters that NFKD does not split into a base letter and a mark ("Ødegaard" lost its first letter in slugs
# and wouldn't match "Odegaard"; DECISIONS #64). Used by ``fold`` and ``programme_form.slugify``.
TRANSLITERATE = str.maketrans(
    {"ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "ł": "l", "Ł": "L", "ß": "ss",
     "đ": "d", "Đ": "D", "þ": "th", "Þ": "TH", "ð": "d", "Ð": "D", "ı": "i"}
)  # fmt: skip
_INITIALS = re.compile(r"^(?:\w\.)+$")


def salutation_surname(surname: str) -> str:
    """The surname as it stands alone after the title: "Dear Professor Van der Berg,".

    A Dutch surname keeps its particle in lower case after a first name or initials ("A. van der Berg");
    standing alone, the first particle is capitalised (docs/02 §9 rule 4). Capitalising the first letter
    does exactly that and leaves an ordinary surname unchanged ("'t Hart" has no letter to capitalise first).
    """
    return surname[:1].upper() + surname[1:]


def fold(text: str) -> str:
    """Case- and diacritic-insensitive form: "Müller" → "muller", "Ødegaard" → "odegaard"."""
    decomposed = unicodedata.normalize("NFKD", text.translate(TRANSLITERATE))
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(plain.casefold().split())


def same_surname(a: str, b: str) -> bool:
    """True when two surnames are the same ignoring case and diacritics (Add professor suggestions)."""
    return bool(a.strip()) and fold(a) == fold(b)


@dataclass(frozen=True)
class ParsedName:
    """A name as written, taken apart (docs/02 §9)."""

    written: str
    title: str  # from the highest-ranked title token, else "Professor"
    title_found: bool
    initials: tuple[str, ...]  # upper-case letters, written or derived from the first names
    first_names: tuple[str, ...]
    surname: str  # with its particle, as written ("van den Berg")

    @property
    def salutation(self) -> str:
        """The surname standing alone: "Van den Berg"."""
        return salutation_surname(self.surname)

    @property
    def key(self) -> tuple[str, tuple[str, ...]]:
        """Matching key: surname and initials, case- and diacritic-insensitive."""
        return fold(self.surname), tuple(fold(i) for i in self.initials)


def _title_parts(token: str, cfg: NamesConfig) -> list[str] | None:
    """The titles in a token such as "Prof.", "dr" or "Prof.dr.ir.", or None if it isn't only titles."""
    parts = [p for p in token.casefold().split(".") if p]
    if not parts or any(p not in cfg.title_tokens for p in parts):
        return None
    return [cfg.title_tokens[p] for p in parts]


def parse(name: str, cfg: NamesConfig) -> ParsedName:
    """Take a name apart: title tokens, initials, first names and the surname with its particle."""
    tokens = name.split()
    titles: list[str] = []
    rest: list[str] = []
    for token in tokens:
        found = _title_parts(token, cfg) if not rest else None  # titles come before the name
        if found is not None:
            titles += found
        else:
            rest.append(token)
    if not rest and tokens:  # a name made only of title-like words: keep it as the surname
        rest, titles = tokens[-1:], []
    initials: list[str] = []
    while rest[:-1] and _INITIALS.match(rest[0]):
        initials += [p.upper() for p in rest.pop(0).split(".") if p]
    particles = {p.casefold() for p in cfg.particles}
    start = len(rest) - 1
    while start > 0 and rest[start - 1].casefold() in particles:
        start -= 1
    first_names = tuple(rest[:start])
    surname = " ".join(rest[start:])
    if not initials:
        initials = [n[0].upper() for n in first_names if n[:1].isalpha()]
    ranked = [t for t in cfg.title_precedence if t in titles]
    return ParsedName(
        written=name,
        title=ranked[0] if ranked else DEFAULT_TITLE,
        title_found=bool(ranked),
        initials=tuple(initials),
        first_names=first_names,
        surname=surname,
    )


def match(a: ParsedName, b: ParsedName) -> str | None:
    """The kind of match: "exact" (surname and initials), "surname" (one has no initials) or None."""
    (sa, ia), (sb, ib) = a.key, b.key
    if not sa or sa != sb:
        return None
    if ia and ib:
        if ia != ib:
            return None
        # initials may be derived from first names (rule 2): "Lise" and "Lars" are not one person, so with
        # first names written on both sides they must agree, else it is only offered (DECISIONS #105, D1 a)
        fa, fb = (tuple(fold(n) for n in x.first_names) for x in (a, b))
        return "exact" if not (fa and fb) or fa == fb else "surname"
    return "surname"
