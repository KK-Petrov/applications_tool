"""Exact checks on a generated request email (rule 13: computed in Python, never asked of the model).

The eval graders (tests/evals/email/graders.py, docs/04 §4) and the app use the same functions: the
graders count failures for C-2b-02 (facts) and C-2b-03 (form); the app shows them as warnings next to the
draft so the owner sees a wrong deadline or a missing salutation before copying it.

- **Facts**: the salutation names the professor's surname; every course name appears; every selected
  programme's ``short_name`` appears with its own exact ``deadline_text`` on the same line; no unselected
  programme's short name, school or alias does (case-sensitive, whole words, possessives count).
- **Form**: 180–280 words (docs/02 §7, the whole body); the first line is exactly
  ``Dear {title} {surname},``; an anticipated achievement is never stated without "expect", "anticipat"
  or "on track" in its sentence; months are spelled out, never "Jan", "Sept." and so on (DECISIONS #49).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from hub.models.biography import BiographyEntry, EntryState
from hub.models.programme import Programme
from hub.rules.words import count_words

_HEDGE = re.compile(r"expect|anticipat|on track", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
_PAREN = re.compile(r"\s*\([^)]*\)")
# Abbreviated month names; the email spells months out (DECISIONS #49). "May" has no short form.
_SHORT_MONTH = re.compile(r"(?<!\w)(Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)(?!\w)")


def _first_line(body: str) -> str:
    return next((line.strip() for line in body.splitlines() if line.strip()), "")


def _salutation(payload: dict[str, Any]) -> str:
    prof = payload["professor"]
    return f"Dear {prof['title']} {prof['surname']},"


def _spans(term: str, text: str) -> list[tuple[int, int]]:
    """Where ``term`` occurs as whole words, case-sensitive, any run of whitespace between its words."""
    words = [re.escape(w) for w in term.split()]
    return [m.span() for m in re.finditer(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)", text)]


def unselected_terms(selected: Iterable[Programme], others: Iterable[Programme]) -> list[str]:
    """Names that would point to a programme the email must not mention (docs/04 §4 fact grader).

    A term is dropped when it equals or is part of a selected programme's own names, so "Columbia" is not
    flagged when Columbia MSFE is selected (as in docs/02 §8 step 2).
    """
    own = [n for p in selected for n in (p.name, p.short_name, p.school, p.unit or "", *p.aliases) if n]
    terms: list[str] = []
    for p in others:
        for term in (p.short_name, p.school, *p.aliases):
            if term and term not in terms and not any(term in name for name in own):
                terms.append(term)
    return terms


def anticipated_terms(entries: Iterable[BiographyEntry]) -> list[str]:
    """Key phrases of anticipated entries: the title without notes in brackets ("Cum laude (expected)")."""
    terms = []
    for e in entries:
        if e.state is EntryState.ANTICIPATED:
            term = _PAREN.sub("", e.title).strip()
            if term:
                terms.append(term)
    return terms


def fact_problems(body: str, payload: dict[str, Any], unselected: Iterable[str]) -> list[str]:
    """Facts missing, changed or out of place (C-2b-02). Empty means the email states the facts exactly."""
    problems: list[str] = []
    surname = payload["professor"]["surname"]
    if surname not in _first_line(body):
        problems.append(f"The salutation does not name the surname {surname}")
    for course in payload["professor"]["courses"]:
        if course["name"] and course["name"] not in body:
            problems.append(f"The course name “{course['name']}” is missing")
    for p in payload["programmes"]:
        if p["short_name"] not in body:
            problems.append(f"{p['short_name']} is not named")
        lines = body.splitlines()
        if not any(p["short_name"] in line and p["deadline_text"] in line for line in lines):
            # its own deadline, on the programme's line: a swapped deadline is a fact failure (DECISIONS #52)
            problems.append(
                f"{p['short_name']}: the deadline “{p['deadline_text']}” is missing, changed or not on the "
                "programme's line"
            )
    found = [(span, term) for term in unselected for span in _spans(term, body)]
    reported: list[str] = []
    for (
        start,
        end,
    ), term in found:  # a mention inside a longer one ("MIT" in "MIT Sloan MFin") is not separate
        inside = any(s <= start and end <= e and (s, e) != (start, end) for (s, e), _ in found)
        if not inside and term not in reported:
            reported.append(term)
    for term in reported:
        problems.append(f"Mentions {term}, which is not one of the programmes in this request")
    return problems


def form_problems(body: str, payload: dict[str, Any], anticipated: Iterable[str]) -> list[str]:
    """Length, salutation and anticipated achievements (C-2b-03). Empty means the form is right."""
    problems: list[str] = []
    limits = payload["constraints"]
    words = count_words(body)
    if not limits["min_words"] <= words <= limits["max_words"]:
        problems.append(f"{words} words; the email must have {limits['min_words']}–{limits['max_words']}")
    expected = _salutation(payload)
    if _first_line(body) != expected:
        problems.append(f"The salutation must be exactly “{expected}”")
    names = {
        w for n in (payload["professor"]["name"], payload["owner"]["name"]) for w in re.findall(r"\w+", n)
    }
    short = sorted(set(_SHORT_MONTH.findall(body)) - names)  # "Jan" is also a Dutch first name
    if short:
        problems.append(f"Write months in full, not {', '.join(short)}")
    sentences = [s for s in _SENTENCE_END.split(body) if s.strip()]
    for term in anticipated:
        for sentence in sentences:
            if term.casefold() in sentence.casefold() and not _HEDGE.search(sentence):
                problems.append(f"States “{term}” as achieved, but it is anticipated: “{sentence.strip()}”")
    return problems


def subject_matches(subject: str, payload: dict[str, Any]) -> bool:
    """The subject is the one built in Python (reported, not gated)."""
    return subject.strip() == payload["constraints"]["subject"]


def app_warnings(
    subject: str, body: str, payload: dict[str, Any], unselected: Iterable[str], anticipated: Iterable[str]
) -> list[str]:
    """Every exact-check problem as a warning for the owner, shown with the draft, one line per problem.

    The grader counts facts and form separately, so one slip can appear in both lists (a wrong salutation;
    an unnamed programme that therefore also lacks its deadline line). Here each is said once.
    """
    facts = fact_problems(body, payload, unselected)
    form = form_problems(body, payload, anticipated)
    if any(p.startswith("The salutation must be exactly") for p in form):
        facts = [p for p in facts if not p.startswith("The salutation does not name")]
    unnamed = {p.removesuffix(" is not named") for p in facts if p.endswith(" is not named")}
    facts = [p for p in facts if not any(p.startswith(f"{name}: the deadline") for name in unnamed)]
    warnings = [*facts, *form]
    if not subject_matches(subject, payload):
        warnings.append(f"The subject differs from “{payload['constraints']['subject']}”")
    sign_off = payload["constraints"]["sign_off"]
    if sign_off not in body:
        warnings.append(f"The email does not close with “{sign_off}”")
    return warnings
