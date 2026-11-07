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
    labels: tuple[str, ...], months: tuple[str, ...], marker: str, marker_fills_slot: bool
) -> _Patterns:
    def words(label: str) -> str:
        return r"[\s\-]*".join(re.escape(w) for w in re.split(r"[\s\-]+", label.strip()))

    alternatives = "|".join(words(lb) for lb in sorted(set(labels), key=len, reverse=True))
    # the label, then an optional bracketed abbreviation such as "(BSN)"
    label = re.compile(rf"(?<![\w])(?:{alternatives})(?![\w])(?:\s*\([^)\n\d]{{0,30}}\))?", re.IGNORECASE)
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
    # letters then digits, optionally in groups ("123 456 782", "1234.56.782"); a group never swallows the
    # start of a date ("598431 12-03-2001" is two values)
    number = r"[A-Za-z]{0,3}\d{2,}(?:[ .]\d{2,4}(?![\d\-/.]))*"
    ssn = r"\d{3}-\d{2}-\d{4}"  # US social security number
    # the marker is a value only in the Send mode (#73): at Read a document's own "[removed]" proves nothing
    removed = re.escape(marker) + "|" if marker_fills_slot else ""
    value = re.compile(rf"(?<![\w])(?:{removed}{date}|{ssn}|{number})(?![\w])", re.IGNORECASE)
    bsn = re.compile(r"(?<![\w.,\-/])\d{9}(?![\w.,\-/])")
    file_date = re.compile(r"\d{1,2}[-./_]\d{1,2}[-./_]\d{2,4}|\d{4}[-./_]\d{1,2}[-./_]\d{1,2}")
    near_date = re.compile(rf"(?:{month}|{roman})(?![a-z])|'\d", re.IGNORECASE)
    standalone_ssn = re.compile(r"(?<![\w\-])\d{3}-\d{2}-\d{4}(?![\w\-])")
    return _Patterns(label, value, bsn, file_date, near_date, standalone_ssn, marker, marker_fills_slot)


def _p(cfg: CoursesConfig, marker_fills_slot: bool = False) -> _Patterns:
    labels, months = tuple(cfg.identifier_labels.all), tuple(cfg.month_names.all)
    return _patterns(labels, months, cfg.removed_marker, marker_fills_slot)


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
    for start, end in sorted(spans, reverse=True):
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


def strip(text: str, cfg: CoursesConfig, *, strict: bool = True, marker_fills_slot: bool = False) -> str:
    """``text`` with every label-anchored identifier and every 11-check-valid standalone 9-digit number
    replaced by ``cfg.removed_marker``. With ``strict`` (a document before it is sent), a label whose value
    can't be found and taken whole raises ``UnresolvedIdentifier`` (fail closed); without it (fields the model
    returned) such a label is left. ``marker_fills_slot`` is the Send mode (#73): the text was stripped at
    Read, so a marker may stand in a label's value slot; it is never an ID-shaped token, so a number after
    it still stops the import. At Read the marker is never a value."""
    p = _p(cfg, marker_fills_slot)
    marker = cfg.removed_marker
    lines = normalise(text).split(NEWLINE)
    pending: list[tuple[int, str, int | None]] = []  # (line number, label, table column) for the next line
    for i, line in enumerate(lines):
        if pending and line.strip():
            spans = _next_line_values(line, pending, p)
            if spans is None or p.label.search(line):
                if strict:
                    raise _unresolved(pending[0][0], pending[0][1])
            else:
                lines[i] = _replace(line, spans, marker)
                pending = []
                continue
            pending = []
        labels = list(p.label.finditer(line))
        spans: list[tuple[int, int]] = []
        for n, label in enumerate(labels):
            end = labels[n + 1].start() if n + 1 < len(labels) else len(line)
            if TAB in line[label.end() : end]:
                end = label.end() + line[label.end() : end].index(TAB)  # a table cell ends at its tab
            segment = line[label.end() : end]
            # on its own line any number of 2+ digits is the label's value (a short student number is one too)
            found = next(iter(_values(segment, p, SAME_LINE_MIN_DIGITS)), None)
            if found is None and TAB in line[label.end() :] and _has_digits_outside_labels(line, p):
                # a key/value row ("Student number<TAB>512345"): the value is in the next cell (#67)
                cells = _cell_spans(line)
                column = line[: label.start()].count(TAB)
                if column + 1 >= len(cells):
                    if strict:
                        raise _unresolved(i + 1, label.group(0).strip())
                    continue
                start, end_cell = cells[column + 1]
                cell = line[start:end_cell]
                value = next(iter(_values(cell, p, SAME_LINE_MIN_DIGITS)), None)
                if value is None or not _taken_whole(cell, value, p):
                    if strict:
                        raise _unresolved(i + 1, label.group(0).strip())
                    continue
                spans.append((start + value.start(), start + value.end()))
                continue
            if found is None:
                if any(ch.isdigit() for ch in segment) and strict:
                    raise _unresolved(i + 1, label.group(0).strip())  # digits, but no value taken whole
                column = line[: label.start()].count(TAB) if TAB in line else None
                pending.append((i + 1, label.group(0).strip(), column))
            elif not _taken_whole(segment, found, p):
                if strict:
                    raise _unresolved(i + 1, label.group(0).strip())
                # a field Claude returned: remove every candidate after the label (over-removal is the safe
                # side; review round 3: "Student ID (512345): 598431" kept both)
                spans.extend(
                    (label.end() + m.start(), label.end() + m.end())
                    for m in _values(segment, p, SAME_LINE_MIN_DIGITS)
                )
            else:
                spans.append((label.end() + found.start(), label.end() + found.end()))
        lines[i] = _replace(line, spans, marker)
    if pending and strict:
        raise _unresolved(pending[0][0], pending[0][1])
    out = NEWLINE.join(lines)
    out = p.ssn.sub(marker, out)  # a US social security number shape, with or without a label (#67)
    return p.bsn.sub(lambda m: marker if passes_bsn_check(m.group(0)) else m.group(0), out)


def _next_line_values(
    line: str, pending: list[tuple[int, str, int | None]], p: _Patterns
) -> list[tuple[int, int]] | None:
    """The spans of the pending labels' values on the next line, or None if any can't be taken whole.
    A tab-separated label row and value row are matched column by column; otherwise the k pending labels
    take the line's first k values (5+ digits or a date), in order."""
    if TAB in line and all(col is not None for _, _, col in pending):
        cells = _cell_spans(line)
        spans = []
        for _, _, col in pending:
            if col is None or col >= len(cells):
                return None
            start, end = cells[col]
            cell = line[start:end]
            found = next(iter(_values(cell, p, SAME_LINE_MIN_DIGITS)), None)
            if found is None or not _taken_whole(cell, found, p):
                return None
            spans.append((start + found.start(), start + found.end()))
        return spans
    # without tabs, more ID-shaped tokens (5+ digits, or a date; never the marker) than labels: the app can't
    # tell which is the identifier, so the import stops (user decision, #73)
    id_shaped = [m for m in _values(line, p, MIN_ID_DIGITS) if m.group(0) != p.marker]
    markers = line.count(p.marker) if p.marker_fills_slot else 0  # Send mode: a marker takes a slot
    if len(id_shaped) + markers > len(pending):
        return None
    # without tabs: the values must lead the line, in order ("598431 12-03-2001 BSc …"); anything else first
    # (a course code, a word) means the value isn't there and the import stops (#67)
    spans: list[tuple[int, int]] = []
    pos = len(line) - len(line.lstrip())
    for n in range(len(pending)):
        found = next((m for m in _values(line[pos:], p, SAME_LINE_MIN_DIGITS) if m.start() == 0), None)
        if found is None:
            return None
        spans.append((pos + found.start(), pos + found.end()))
        rest = line[pos + found.end() :]
        skipped = _SEPARATORS.match(rest)
        after = rest[skipped.end() :] if skipped else rest
        # taken whole, as on the label's own line (review round 3, F1): no month word, Roman month or 'YY
        # right after it, and after the last value no digit ("12/3 2001", "5123-45", "2019 512345")
        if p.near_date.match(after) or (n == len(pending) - 1 and after[:1].isdigit()):
            return None
        pos += found.end() + (skipped.end() if skipped else 0)
    # nothing ID-shaped may remain on the line after the labels' values (F4: Read and Send count alike)
    tail = line[pos:]
    if _values(tail, p, MIN_ID_DIGITS) or (p.marker_fills_slot and p.marker in tail):
        return None
    return spans


def _has_digits_outside_labels(line: str, p: _Patterns) -> bool:
    rest = p.label.sub(" ", line)
    return any(ch.isdigit() for ch in rest) or (p.marker_fills_slot and p.marker in rest)


def _unresolved(line_no: int, label: str) -> UnresolvedIdentifier:
    return UnresolvedIdentifier(
        f'The document mentions "{label}" on line {line_no}, but the app could not find its value to remove '
        "whole. Nothing was sent to Claude. Remove that part of the document, or paste the text without it."
    )


def clean_filename(name: str, cfg: CoursesConfig) -> str:
    """The original file's name as stored in the source header: dates and runs of 6 or more digits removed
    (a file name has no label to anchor on, and may hold a student number or a birth date); one line only."""
    p = _p(cfg)
    out = p.file_date.sub(cfg.removed_marker, " ".join(normalise(name).replace("\r", " ").split(NEWLINE)))
    return re.sub(r"\d{6,}", cfg.removed_marker, out)
