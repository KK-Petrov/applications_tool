"""Identifier stripping in imported documents (docs/02 §10; rule 13; DECISIONS #44, #64, #65).

A number or date is removed only when an identifier label (``courses.identifier_labels``, English and Dutch:
student number, date of birth, BSN, studentnummer, geboortedatum …) anchors it:

1. **Same line:** the first number (2+ digits) or date after the label on its line, before the next label (or,
   in a table row, before the next tab).
2. **Label on one line, value on the next:** a label with no value after it on its line ("Student ID" /
   "S2034567") takes the first number (5+ digits) or date on the next non-empty line.
3. **Table:** a tab-separated label row followed by a value row is matched column by column
   ("Studentnummer  Opleiding  Geboortedatum" / "598431  56966 BSc …  12-03-2001"); without tabs (a PDF row)
   the k labels without a value take the next line's first k values, in order.

**Fail closed:** a value must be taken whole. If digits stand right before or right after it (past separators,
as in an unknown date format "12/3 2001"), or a label's value can't be found, the import stops with
``UnresolvedIdentifier`` and nothing is sent to the model. A standalone 9-digit number is treated as a BSN,
and removed, only if it passes the 11-check. Course codes, grades, credits and years are kept: nothing else is
touched. Each removed value is replaced by ``courses.removed_marker``. The same stripping runs again on every
field the model returns (there, not strict).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hub.config import CoursesConfig

MIN_ID_DIGITS = 5  # on the next line, a number shorter than this is not taken for the label's value
SAME_LINE_MIN_DIGITS = 2  # on the label's own line, any number this long is its value
TAB = "\t"
NEWLINE = "\n"


# Unicode Default_Ignorable_Code_Point ranges that are not category Cf (combining grapheme joiner, Hangul
# fillers, variation selectors, ...); every Cf character is removed as well (DECISIONS #69, #74)
DEFAULT_IGNORABLE = (
    (0x034F, 0x034F),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)


def _ignorable(ch: str) -> bool:
    code = ord(ch)
    return unicodedata.category(ch) == "Cf" or any(lo <= code <= hi for lo, hi in DEFAULT_IGNORABLE)


def normalise(text: str) -> str:
    """``text`` without invisible characters (every format character, category Cf: zero-width space, joiners,
    byte-order mark, soft hyphen, direction marks; and every other default-ignorable code point: combining
    grapheme joiner, variation selectors, Hangul fillers) and with every space character (category Zs:
    no-break, narrow no-break, thin, figure ...) as a plain space. Runs before any label or value matching, so
    an invisible character can neither hide a label ("Student<U+200B>number") nor split a value
    ("51<U+200B>2345"). The text shown, sent and stored is this normalised text (DECISIONS #69, #74)."""
    return "".join(" " if unicodedata.category(ch) == "Zs" else ch for ch in text if not _ignorable(ch))


class UnresolvedIdentifier(Exception):
    """An identifier label whose value could not be found and removed whole; the message is for the user."""


@dataclass(frozen=True)
class _Patterns:
    label: re.Pattern[str]
    value: re.Pattern[str]
    bsn: re.Pattern[str]
    file_date: re.Pattern[str]
    near_date: re.Pattern[str]  # a month word, Roman month or 'YY next to a value
    ssn: re.Pattern[str]
    marker: str
    marker_fills_slot: bool  # Send mode: a marker may fill a label's value slot (#73)


@cache
def _patterns(
    labels: tuple[str, ...],
    date_labels: tuple[str, ...],
    name_words: tuple[str, ...],
    months: tuple[str, ...],
    marker: str,
    marker_fills_slot: bool,
) -> _Patterns:
    def words(label: str) -> str:
        return r"[\s\-]*".join(re.escape(w) for w in re.split(r"[\s\-]+", label.strip()))

    month = "|".join(re.escape(m) for m in sorted(set(months), key=len, reverse=True))
    ordinal = r"(?:st|nd|rd|th|ste|de|e)?"  # 12th, 3rd; Dutch 1ste, 12de, 12e
    roman = "XII|XI|X|IX|VIII|VII|VI|V|IV|III|II|I"  # a month as a Roman numeral (12.III.2001)
    date = "|".join(
        [
            r"\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}",  # 12-03-2001, 03/12/2001, 12.03.2001
            r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}",  # 2001-03-12, 2001/03/12, 2001.03.12
            # 12 March 2001, 12-MAR-01, 12 maart '01
            rf"\d{{1,2}}{ordinal}\.?[\s\-/.]+(?:of\s+)?(?:{month})\.?,?[\s\-/.]+(?:\d{{4}}|'?\d{{2}})(?!\d)",
            rf"\d{{4}}[\s\-/.]+(?:{month})\.?[\s\-/.]+\d{{1,2}}(?!\d)",  # 2001-Mar-12
            rf"\d{{1,2}}\.(?:{roman})\.\d{{2,4}}",  # 12.III.2001
            rf"(?:{month})\.?\s+\d{{1,2}}{ordinal},?\s+\d{{4}}",  # March 12th, 2001
            rf"(?:{month})\.?\s+\d{{4}}",  # March 2001
        ]
    )
    alternatives = "|".join(words(lb) for lb in sorted(set(labels), key=len, reverse=True))
    # a date label ("born", "geboren") is a label by default (#89, #90, data-integrity r10 F1); none only
    # (a) right after a title token, an initial or a particle (`names`: "Prof. M. Born", "Dr. K. Geboren",
    # "M. van Born"), or (b) when the first thing after it, past an optional "(…)" and separators, is a word
    # that is not a month ("the Born rule", "Born Rule Seminar"). A colon after it always makes it a label.
    dated = "|".join(words(lb) for lb in sorted(set(date_labels), key=len, reverse=True))
    if dated:
        before = [r"(?<![A-Z]\. )", r"(?<![A-Z]\.)"]  # an initial, with or without a space
        for token in sorted(set(name_words)):
            t = re.escape(token)
            before += [rf"(?<!(?<![\w]){t} )", rf"(?<!(?<![\w]){t}\. )", rf"(?<!(?<![\w]){t}\.)"]
        word_after = rf"[ \t]*(?:\([^)\n]*\))?[ \t;,.\-–#=]*(?!(?:{month})(?![^\W\d_]))[^\W\d_]"
        alternatives += rf"|{''.join(before)}(?:{dated})(?!{word_after})"
    # the label, then an optional bracketed abbreviation such as "(BSN)"; never a bracket holding a digit or
    # the marker, which is the label's value ("Student number ([removed])" at Send; round 8, F3)
    label = re.compile(rf"(?<![\w])(?:{alternatives})(?![\w])(?:\s*\([^)\n\d\[\]]{{0,30}}\))?", re.IGNORECASE)
    # letters then digits, optionally in groups ("123 456 782", "1234.56.782"); a group never swallows the
    # start of a date ("598431 12-03-2001", "598431 12 maart 2001" are two values; round 8, F8)
    number = rf"[A-Za-z]{{0,3}}\d{{2,}}(?:[ .]\d{{2,4}}(?![\d\-/.])(?![\s\-/.]+(?:{month})(?![a-z])))*"
    ssn = r"\d{3}-\d{2}-\d{4}"  # US social security number
    # a year-serial register number ("2023-004512"); 5+ digits after the hyphen, so an academic year range
    # ("2021-2022") and a short "5123-45" are not one value (DECISIONS #76)
    serial = r"\d{4}-\d{5,}"
    # the marker is a value only in the Send mode (#73): at Read a document's own "[removed]" proves nothing
    removed = re.escape(marker) + "|" if marker_fills_slot else ""
    value = re.compile(rf"(?<![\w])(?:{removed}{date}|{ssn}|{serial}|{number})(?![\w])", re.IGNORECASE)
    bsn = re.compile(r"(?<![\w.,\-/])\d{9}(?![\w.,\-/])")
    file_date = re.compile(r"\d{1,2}[-./_]\d{1,2}[-./_]\d{2,4}|\d{4}[-./_]\d{1,2}[-./_]\d{1,2}")
    near_date = re.compile(rf"(?:{month}|{roman})(?![a-z])|'\d", re.IGNORECASE)
    standalone_ssn = re.compile(r"(?<![\w\-])\d{3}-\d{2}-\d{4}(?![\w\-])")
    return _Patterns(label, value, bsn, file_date, near_date, standalone_ssn, marker, marker_fills_slot)


def _p(cfg: CoursesConfig, marker_fills_slot: bool = False) -> _Patterns:
    labels, months = tuple(cfg.identifier_labels.all), tuple(cfg.month_names.all)
    dated = tuple(cfg.date_labels.all)
    return _patterns(labels, dated, _name_words(), months, cfg.removed_marker, marker_fills_slot)


@cache
def _name_words() -> tuple[str, ...]:
    """Title tokens and particles (config `names`): before "born"/"geboren" they make it a surname (#92)."""
    from hub.config import load_config

    names = load_config().names
    return tuple(sorted({*names.title_tokens, *names.particles}))


def passes_bsn_check(digits: str) -> bool:
    """The Dutch BSN 11-check: 9·d1 + 8·d2 + … + 2·d8 − 1·d9 is a multiple of 11 (and not all zeros)."""
    if not re.fullmatch(r"\d{9}", digits) or digits == "000000000":
        return False
    total = sum(int(d) * w for d, w in zip(digits[:8], range(9, 1, -1), strict=True)) - int(digits[8])
    return total % 11 == 0


def _values(text: str, p: _Patterns, min_digits: int = MIN_ID_DIGITS) -> list[re.Match[str]]:
    """Possible identifiers in ``text``: a date, or a number with ``min_digits`` or more digits."""
    out = []
    for m in p.value.finditer(text):
        if m.group(0) == p.marker:
            out.append(m)
            continue
        digits = sum(ch.isdigit() for ch in m.group(0))
        is_date = bool(re.search(r"[-/.]\d|[A-Za-z]{3}", m.group(0))) and not re.fullmatch(
            r"[A-Za-z]{0,3}\d[\d .]*", m.group(0)
        )
        if is_date or digits >= min_digits:
            out.append(m)
    return out


def _replace(line: str, spans: list[tuple[int, int]], marker: str) -> str:
    """``line`` with each span replaced by ``marker``; overlapping or repeated spans are merged first, so no
    text is replaced twice (data-integrity review round 6, F1: "[removed]ed]")."""
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    for start, end in reversed(merged):
        line = line[:start] + marker + line[end:]
    return line


_SEPARATORS = re.compile(r"[\s,./\-:]*")


def _taken_whole(segment: str, match: re.Match[str], p: _Patterns) -> bool:
    """True when ``match`` is the whole identifier in its segment: no digit before it; right after it (past
    separators) no digit, month word, Roman month or 'YY ("12/3 2001", "12-MAR-01" would leave part of a date
    behind); and no other number later in the segment ("512345, previously 498765"). Fail closed (#67)."""
    if any(ch.isdigit() for ch in segment[: match.start()]):
        return False
    rest = segment[match.end() :]
    skipped = _SEPARATORS.match(rest)
    after = rest[skipped.end() :] if skipped else rest
    if after[:1].isdigit() or p.near_date.match(after):
        return False
    return re.search(r"\d", rest) is None


def _cell_spans(line: str) -> list[tuple[int, int]]:
    spans, start = [], 0
    for part in line.split(TAB):
        spans.append((start, start + len(part)))
        start += len(part) + 1
    return spans


# between a label and its value only separators ("Student number: 512345", "Date of birth - 12 March 2001",
# "Student number (512345)")
_GAP = re.compile(r"[\s:;,.\-–#=(]*")
_LETTER = re.compile(r"[^\W\d_]")
_COLON_ONLY = re.compile(r"[ ]*:[ ]*")  # the rest of a label's cell is just its colon ("Student number:")


class _Outside(Exception):
    """A label outside the four layouts, or one whose value can't be taken whole in its layout. ``lines``
    are the lines involved (the label's line, and the value line it was matched against)."""

    def __init__(self, label: re.Match[str], lines: list[int]) -> None:
        super().__init__(label.group(0))
        self.label = label.group(0).strip()
        self.lines = lines


def strip(
    text: str, cfg: CoursesConfig, *, strict: bool = True, marker_fills_slot: bool = False, owner: str = ""
) -> str:
    """``text`` with every identifier removed: the values of identifier labels in one of four known layouts,
    every 11-check-valid standalone 9-digit number and every US SSN shape, each replaced by
    ``cfg.removed_marker`` (user decision, DECISIONS #84; docs/02 §10). The four layouts:

    1. **"Label: value"**: on a line or in a table cell, each label directly followed (only separators
       between) by its own value, taken whole; several pairs may share a line.
    2. **Label, value on the next line**: a line of one or more labels with no value on it (other words
       allowed), and the next non-empty line, without tabs and without a label, starting with their values
       in order ("Student ID" / "S2034567"; a PDF heading row "Studentnummer Geboortedatum Opleiding" /
       "598431 12-03-2001 BSc ...").
    3. **Label cell, value cell**: a table cell holding one label and nothing else, the next cell holding the
       value alone ("Student number<TAB>512345").
    4. **Heading row, value row**: a table row of headings, at most one label per cell and no value
       anywhere on it, and the next non-empty row, also tabbed and without a label, with each label's value
       alone in its column; a cell no heading heads holds nothing ID-shaped.

    Any other layout around a label is "outside": with ``strict`` (a document before it is sent, and the
    Save step) the import stops with ``UnresolvedIdentifier`` saying what to do; without it (fields Claude
    returned) every candidate on the lines involved is removed (over-removal is the safe side, #74).
    ``marker_fills_slot`` is the Send mode (#73): the marker may stand in a value's place; at Read it never
    does, so a document's own "[removed]" after a label is outside."""
    p = _p(cfg, marker_fills_slot)
    marker = cfg.removed_marker
    lines = normalise(text).split(NEWLINE)
    spans: dict[int, list[tuple[int, int]]] = {}
    i = 0
    while i < len(lines):
        labels = list(p.label.finditer(lines[i]))
        if not labels:
            i += 1
            continue
        try:
            found, i = (_tabbed if TAB in lines[i] else _untabbed)(lines, i, labels, p)
        except _Outside as outside:
            if strict:
                raise _unresolved(i + 1, outside.label) from None
            # a field Claude returned: every candidate on the lines involved goes, and on the next text line
            # too (a value may stand there whatever the layout; over-removal is the safe side, #74)
            involved = set(outside.lines)
            after = _next_text_line(lines, max(involved))
            if after is not None:
                involved.add(after)
            for n in involved:
                spans.setdefault(n, []).extend(m.span() for m in _values(lines[n], p, SAME_LINE_MIN_DIGITS))
            i = max(involved) + 1
            continue
        for n, line_spans in found.items():
            spans.setdefault(n, []).extend(line_spans)
    # beyond labels (real transcript, DECISIONS #93): a line naming the owner, a repeated page header/footer
    # and a bracket holding only an ID-shaped token lose their ID-shaped tokens (fail closed)
    owner_re = _owner_pattern(owner)
    footers = _footer_lines(lines)
    for n, line in enumerate(lines):
        extra = _bracketed_ids(line, p)
        if n in footers or (owner_re is not None and owner_re.search(line)):
            extra += _id_spans(line, p)
        if extra:
            spans.setdefault(n, []).extend(extra)
    for n, line_spans in spans.items():
        lines[n] = _replace(lines[n], line_spans, marker)
    out = NEWLINE.join(lines)
    out = p.ssn.sub(marker, out)  # a US social security number shape, with or without a label (#67)
    return p.bsn.sub(lambda m: marker if passes_bsn_check(m.group(0)) else m.group(0), out)


Found = tuple[dict[int, list[tuple[int, int]]], int]  # spans per line, and the line to go on from

# ID-shaped for the owner, bracket and footer rules (#93, user decision): a run of 5+ digits (groups of 2–4
# after a space or full stop allowed), at most one letter in front and a suffix of up to 3 letters
# (512345, 598 431, S2034567, 512345ab), or a date. Never a course code ("FEB11001", "FIN 3010"), a year
# range ("2021-2022"), credits ("20.0") or a page number ("1/9").
_ID_TOKEN = re.compile(r"(?<![\w.,/\-])[A-Za-z]?\d{2,}(?:[ .]\d{2,4})*[A-Za-z]{0,3}(?![\w])(?![.,/\-]\d)")
_BRACKET = re.compile(r"\(([^()\n]*)\)")
# a page number: "1/9", "1 / 9", "page 1", "Page 1 of 9", "pagina 1 van 9", "blz. 1"
_PAGE_TOKEN = re.compile(
    r"(?<![\w/.\-])\d{1,3}\s*/\s*\d{1,3}(?![\w/.\-])"
    r"|(?<![\w])(?:page|pagina|blz\.?|p\.)\s*\d{1,3}(?:\s*(?:of|van|/)\s*\d{1,3})?(?![\w])",
    re.IGNORECASE,
)


def _is_date(token: str) -> bool:
    return bool(re.search(r"[-/.]\d|[A-Za-z]{3}", token)) and not re.fullmatch(
        r"[A-Za-z]{0,3}\d[\d .]*", token
    )


def _id_spans(line: str, p: _Patterns) -> list[tuple[int, int]]:
    """Every ID-shaped token on ``line`` (see ``_ID_TOKEN``): numbers of 5+ digits and dates."""
    out = [
        m.span() for m in _ID_TOKEN.finditer(line) if sum(ch.isdigit() for ch in m.group(0)) >= MIN_ID_DIGITS
    ]
    out += [m.span() for m in p.value.finditer(line) if m.group(0) != p.marker and _is_date(m.group(0))]
    return out


def _bracketed_ids(line: str, p: _Patterns) -> list[tuple[int, int]]:
    """Rule 2 (#93): a bracket holding only an ID-shaped token ("(512345)", "( 598431 )", "(12-03-2001)")
    loses it wherever it stands; "( 20.0 )", "(FEB13003)", "(2021-2022)" and "(30)" are kept."""
    out = []
    for m in _BRACKET.finditer(line):
        inner = m.group(1)
        start = m.start(1) + len(inner) - len(inner.lstrip())
        token = inner.strip()
        if not token:
            continue
        spans = _id_spans(token, p)
        if any(a == 0 and b == len(token) for a, b in spans):
            out.append((start, start + len(token)))
    return out


def _footer_lines(lines: list[str]) -> set[int]:
    """Rule 3 (#93): lines holding a page number whose text apart from it stands on 2+ lines (a page header
    or footer printed on every page)."""
    keys: dict[str, list[int]] = {}
    for n, line in enumerate(lines):
        if _PAGE_TOKEN.search(line):
            keys.setdefault(" ".join(_PAGE_TOKEN.sub("#", line).split()).casefold(), []).append(n)
    return {n for found in keys.values() if len(found) >= 2 for n in found}


@cache
def _owner_pattern(owner: str) -> re.Pattern[str] | None:
    """Rule 1 (#93): the owner's surname (with its particle) as whole words, any case. It covers the full
    name and "initials surname" ("J.A. Example") too; a one-word name is matched as it is."""
    if not owner.strip():
        return None
    from hub.config import load_config
    from hub.rules.names import parse

    surname = parse(owner, load_config().names).surname or owner
    words = r"\s+".join(re.escape(w) for w in surname.split())
    return re.compile(rf"(?<![\w]){words}(?![\w])", re.IGNORECASE)


def _untabbed(lines: list[str], i: int, labels: list[re.Match[str]], p: _Patterns) -> Found:
    """Layouts 1 and 2 on a line without tabs."""
    line = lines[i]
    if _id_shaped(line[: labels[0].start()], p):
        raise _Outside(labels[0], [i])  # an ID-shaped value before the first label: whose is it?
    segments = [
        (label, line[label.end() : labels[n + 1].start() if n + 1 < len(labels) else len(line)])
        for n, label in enumerate(labels)
    ]
    valued = [_has_value(segment, p) for _, segment in segments]
    if all(valued):  # layout 1, one or more pairs
        return {i: [_label_value(segment, label.end(), label, [i], p) for label, segment in segments]}, i + 1
    if any(valued):
        raise _Outside(segments[valued.index(False)][0], [i])  # some labels with a value, some without
    if _has_value(line, p):
        raise _Outside(labels[0], [i])  # layout 2's label line holds no value at all ("4521 Student number")
    j = _next_text_line(lines, i)  # layout 2
    if j is None:
        raise _Outside(labels[0], [i])
    if TAB in lines[j] or p.label.search(lines[j]):
        raise _Outside(labels[0], [i, j])
    found = _leading_values(lines[j], len(labels), p)
    if found is None:
        raise _Outside(labels[0], [i, j])
    # a second value line right after it ("598431 …" / "498765 …": two enrolments) is never left unread: the
    # lines that follow without a blank line, tab or label may not start with an ID-shaped value (round 8, F1)
    k = j + 1
    while k < len(lines) and lines[k].strip() and TAB not in lines[k] and not p.label.search(lines[k]):
        if _leads_with_id(lines[k], p):
            raise _Outside(labels[0], [i, j, k])
        k += 1
    return {j: found}, j + 1


def _leads_with_id(text: str, p: _Patterns) -> bool:
    """True if ``text`` starts (past spaces) with an ID-shaped value: 5+ digits or a date; in the Send mode
    also the marker."""
    rest = text.lstrip()
    lead = next((m for m in _values(rest, p, MIN_ID_DIGITS) if m.start() == 0), None)
    return lead is not None and (lead.group(0) != p.marker or p.marker_fills_slot)


def _tabbed(lines: list[str], i: int, labels: list[re.Match[str]], p: _Patterns) -> Found:
    """Layouts 1 (in a cell), 3 and 4 on a table row."""
    line = lines[i]
    cells = _cell_spans(line)
    by_cell: dict[int, list[re.Match[str]]] = {}
    for label in labels:
        by_cell.setdefault(line[: label.start()].count(TAB), []).append(label)
    for in_cell in by_cell.values():
        if len(in_cell) > 1:
            raise _Outside(in_cell[1], [i])  # two labels in one cell (a merged Word header; #82, #83)
    found: list[tuple[int, int]] = []
    heads: list[tuple[int, re.Match[str]]] = []
    for column, (label,) in sorted(by_cell.items()):
        start, end = cells[column]
        if _has_value(line[start : label.start()], p):
            raise _Outside(label, [i])  # a value before the label in its cell
        segment = line[label.end() : end]
        if _has_value(segment, p):  # layout 1 in a cell
            value = _label_value(segment, label.end(), label, [i], p)
            if _continues(line, line[value[0] : value[1]], end, p):
                raise _Outside(label, [i])  # "Date of birth: 12<TAB>03<TAB>2001" (#83)
            found.append(value)
            continue
        nxt = column + 1
        if _COLON_ONLY.fullmatch(segment):
            # "Label:<TAB><TAB>value" (Word tab alignment) is layout 1: with a colon after the label, empty
            # cells before the value are skipped (user decision, DECISIONS #88); without one they are not
            while nxt < len(cells) - 1 and not line[cells[nxt][0] : cells[nxt][1]].strip():
                nxt += 1
        if nxt < len(cells) and nxt not in by_cell:
            next_start, next_end = cells[nxt]
            cell = line[next_start:next_end]
            if _only_a_value(cell, p):  # layout 3
                value = next(iter(_values(cell, p, SAME_LINE_MIN_DIGITS)))
                if not _taken_whole(cell, value, p) or _continues(line, value.group(0), next_end, p):
                    raise _Outside(label, [i])
                found.append((next_start + value.start(), next_start + value.end()))
                continue
        heads.append((column, label))
    if not heads:
        # an ID-shaped cell beside the resolved pairs must be a taken value or the value cell of another
        # (non-label) key cell such as "Ref<TAB>598431"; anything else is outside (round 8, F2)
        for n, (start, end) in enumerate(cells):
            if not _id_shaped(line[start:end], p) or any(start <= a < end for a, _ in found):
                continue
            key = line[cells[n - 1][0] : cells[n - 1][1]] if n else ""
            k = n - 1
            while k > 0 and not line[cells[k][0] : cells[k][1]].strip():
                k -= 1  # "Ref:<TAB><TAB>598431": a key with a colon across empty cells, as #88 (r9 F4)
            if n and not key.strip() and line[cells[k][0] : cells[k][1]].rstrip().endswith(":"):
                key = line[cells[k][0] : cells[k][1]]
            if not (_LETTER.search(key) and not _has_value(key, p) and not p.label.search(key)):
                raise _Outside(labels[0], [i])
        return {i: found}, i + 1
    # layout 4: a heading row holds no value at all (a heading cell beside a "Label: value" cell is outside)
    if found or _has_value(line, p):
        raise _Outside(heads[0][1], [i])
    j = _next_text_line(lines, i)
    if j is None:
        raise _Outside(heads[0][1], [i])
    if TAB not in lines[j] or p.label.search(lines[j]):
        raise _Outside(heads[0][1], [i, j])
    columns = [column for column, _ in heads]
    values = _column_values(lines[j], line, columns, p)
    if values is None:
        raise _Outside(heads[0][1], [i, j])
    # further value rows (a second enrolment, a table of earlier diplomas) are never left unread: until a
    # line without a tab, a heading row (no digits) or a label, no ID-shaped value in a label's column (F1)
    k = j + 1
    while k < len(lines):
        row = lines[k]
        if not row.strip():
            k += 1
            continue
        if TAB not in row or p.label.search(row) or not _has_value(row, p):
            break
        cells_k = _cell_spans(row)
        if any(c < len(cells_k) and _id_shaped(row[cells_k[c][0] : cells_k[c][1]], p) for c in columns):
            raise _Outside(heads[0][1], [i, j, k])
        k += 1
    return {j: values}, j + 1


def _label_value(
    segment: str, offset: int, label: re.Match[str], involved: list[int], p: _Patterns
) -> tuple[int, int]:
    """Layout 1: the span (in the line) of the value right after its label, only separators between, taken
    whole; else outside ("Student number Programme 598431", "Student ID (512345): 598431")."""
    gap = _GAP.match(segment)
    at = gap.end() if gap else 0
    value = next((m for m in _values(segment, p, SAME_LINE_MIN_DIGITS) if m.start() == at), None)
    if value is None or not _taken_whole(segment, value, p):
        raise _Outside(label, involved)
    return offset + value.start(), offset + value.end()


def _column_values(row: str, heading: str, columns: list[int], p: _Patterns) -> list[tuple[int, int]] | None:
    """Layout 4: the spans of the labels' values in ``row``, column by column, or None. Each value stands
    alone in its column's cell, taken whole, not going on in the next cell (#83). A cell that no heading
    heads (no letter in its heading, a label's column aside, or past the heading row) holds nothing
    ID-shaped (#77, #78); a heading row without a non-label heading, followed by a row with words in its
    other cells, is not that row's value row (a course row; #82)."""
    cells = _cell_spans(row)
    labelled = set(columns)
    headed = {
        n
        for n, (a, b) in enumerate(_cell_spans(heading))
        if n not in labelled and _LETTER.search(heading[a:b])
    }
    if not headed and any(n not in labelled and _LETTER.search(row[a:b]) for n, (a, b) in enumerate(cells)):
        return None
    if any(
        n not in headed and n not in labelled and _id_shaped(row[a:b], p) for n, (a, b) in enumerate(cells)
    ):
        return None
    spans = []
    for column in columns:
        if column >= len(cells):
            return None
        start, end = cells[column]
        cell = row[start:end]
        if not _only_a_value(cell, p):
            return None
        value = next(iter(_values(cell, p, SAME_LINE_MIN_DIGITS)))
        if not _taken_whole(cell, value, p) or (
            column + 1 not in labelled and _continues(row, value.group(0), end, p)
        ):
            return None
        spans.append((start + value.start(), start + value.end()))
    return spans


def _leading_values(row: str, k: int, p: _Patterns) -> list[tuple[int, int]] | None:
    """Layout 2: the spans of the first ``k`` values leading ``row``, in order, or None. More ID-shaped
    tokens on the row than labels (#73), anything else first (#67), a value not taken whole (a month word,
    Roman month or 'YY after any; a digit after the last: #74), or anything ID-shaped left after them (#74)
    give None."""
    id_shaped = [m for m in _values(row, p, MIN_ID_DIGITS) if m.group(0) != p.marker]
    markers = row.count(p.marker) if p.marker_fills_slot else 0  # Send mode: a marker takes a slot
    if len(id_shaped) + markers > k:
        return None
    spans: list[tuple[int, int]] = []
    pos = len(row) - len(row.lstrip())
    for n in range(k):
        found = next((m for m in _values(row[pos:], p, SAME_LINE_MIN_DIGITS) if m.start() == 0), None)
        if found is None:
            return None
        spans.append((pos + found.start(), pos + found.end()))
        rest = row[pos + found.end() :]
        skipped = _SEPARATORS.match(rest)
        after = rest[skipped.end() :] if skipped else rest
        if p.near_date.match(after) or (n == k - 1 and after[:1].isdigit()):
            return None
        pos += found.end() + (skipped.end() if skipped else 0)
    if _id_shaped(row[pos:], p):
        return None
    return spans


def _next_text_line(lines: list[str], i: int) -> int | None:
    return next((j for j in range(i + 1, len(lines)) if lines[j].strip()), None)


def _has_value(text: str, p: _Patterns) -> bool:
    """True if ``text`` holds a digit or the marker (a value, or what is left of one)."""
    return any(ch.isdigit() for ch in text) or p.marker in text


def _id_shaped(text: str, p: _Patterns) -> bool:
    """True if ``text`` holds an ID-shaped token: 5+ digits or a date; in the Send mode also the marker."""
    return any(m.group(0) != p.marker for m in _values(text, p, MIN_ID_DIGITS)) or (
        p.marker_fills_slot and p.marker in text
    )


def _only_a_value(cell: str, p: _Patterns) -> bool:
    """True if ``cell`` starts with a value (spaces and punctuation before it allowed) and holds no other
    digit or label after it: "512345", " S2034567.", "598431 (full-time)", but not "Programme code 50952"
    (round 6, F2) or "512345 BSN 123456782" (rounds 6 and 7, F2 and F6)."""
    value = next(iter(_values(cell, p, SAME_LINE_MIN_DIGITS)), None)
    if value is None or re.search(r"[0-9A-Za-z]", cell[: value.start()]):
        return False
    rest = cell[value.end() :]
    return not re.search(r"[0-9]", rest) and not p.label.search(rest)


def _continues(line: str, value: str, cell_end: int, p: _Patterns) -> bool:
    """True if ``value`` is a fragment (fewer than 5 digits and no date) and the cells after ``cell_end``
    go on with it (past tabs and separators a digit, a month word, a Roman month or 'YY): "12<TAB>03<TAB>2001"
    is one date in three cells, "598<TAB>431" one number (round 7, F2). A whole value is not continued:
    "598431<TAB>56966 BSc" keeps the programme code in its own column."""
    if cell_end >= len(line) or line[cell_end] != TAB or _values(value, p, MIN_ID_DIGITS):
        return False
    rest = line[cell_end:].lstrip(TAB + " ,./-:")
    return rest[:1].isdigit() or bool(p.near_date.match(rest))


def _unresolved(line_no: int, label: str) -> UnresolvedIdentifier:
    return UnresolvedIdentifier(
        f'The document mentions "{label}" on line {line_no}, but not in a layout the app can read safely, so '
        "it can't remove its value whole. Nothing was sent to Claude. Paste the text, or put the identifier "
        'as "Label: value" on its own line.'
    )


def clean_filename(name: str, cfg: CoursesConfig) -> str:
    """The original file's name as stored in the source header: dates and runs of 6 or more digits removed
    (a file name has no label to anchor on, and may hold a student number or a birth date); one line only."""
    p = _p(cfg)
    out = p.file_date.sub(cfg.removed_marker, " ".join(normalise(name).replace("\r", " ").split(NEWLINE)))
    return re.sub(r"\d{6,}", cfg.removed_marker, out)
