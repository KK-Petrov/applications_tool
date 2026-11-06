"""Exact checks on a generated request email, in Python (rule 13; docs/04 §4 graders, C-2b-02 and C-2b-03).

The same functions grade the eval set and show warnings in the app, so a check is never asked of the model.
"""

from __future__ import annotations

import pytest

from hub.ai.email_checks import (
    anticipated_terms,
    fact_problems,
    form_problems,
    subject_matches,
    unselected_terms,
)
from hub.models.biography import BiographyEntry
from hub.store import Store

PAYLOAD = {
    "owner": {"name": "Alex Example"},
    "professor": {
        "name": "Prof. Dr. Eva Jansen",
        "title": "Professor",
        "surname": "Jansen",
        "university": "RSM",
        "courses": [{"name": "Corporate Finance (FIN-101)", "code": None, "description": "x", "grade": None,
                     "term": None, "credits": None}],
        "interaction": None,
        "tone": "formal",
    },
    "programmes": [
        {"short_name": "Princeton MFin", "full_name": "Princeton Master in Finance",
         "deadline_text": "15 December 2026, 11:59 pm ET", "submission": None, "letters_note": None},
        {"short_name": "Booth MiF", "full_name": "Chicago Booth Asness and Liew Master in Finance",
         "deadline_text": "7 January 2027", "submission": None, "letters_note": None},
    ],
    "biography": [],
    "constraints": {"min_words": 180, "max_words": 280,
                    "subject": "Request for a letter of recommendation: master's programmes in finance",
                    "sign_off": "Kind regards,"},
}  # fmt: skip


def _body(words: int = 200, *, salutation: str = "Dear Professor Jansen,", extra: str = "") -> str:
    """A well-formed email of exactly ``words`` words (salutation and sign-off included)."""
    head = (
        f"{salutation}\n\n"
        "I took Corporate Finance (FIN-101) with you.\n\n"
        "- Princeton MFin: 15 December 2026, 11:59 pm ET\n"
        "- Booth MiF: 7 January 2027\n\n"
    )
    tail = f" {extra}\n\nKind regards,\nAlex Example"  # leading space: never glued to the filler
    from hub.rules.words import count_words

    filler = max(0, words - count_words(head) - count_words(tail))
    return head + " ".join(["word"] * filler) + tail


@pytest.fixture
def others(config) -> list[str]:
    """Flag terms of the programmes not selected (MIT Sloan MFin, Columbia MSFE)."""
    programmes = Store.from_config(config).load_all().programmes
    selected = [p for p in programmes if p.id in ("princeton-mfin", "booth-mif")]
    rest = [p for p in programmes if p not in selected]
    return unselected_terms(selected, rest)


def test_a_correct_email_has_no_problems(others: list[str]) -> None:
    body = _body()
    assert fact_problems(body, PAYLOAD, others) == []
    assert form_problems(body, PAYLOAD, ["cum laude"]) == []


def test_unselected_terms_cover_short_name_school_and_aliases(others: list[str]) -> None:
    for term in (
        "MIT Sloan MFin",
        "Columbia MSFE",
        "Columbia University",
        "Massachusetts Institute of Technology",
    ):
        assert term in others
    assert "Princeton" not in others and "Booth" not in others


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        (("15 December 2026, 11:59 pm ET", "15 Dec 2026, 11:59 pm ET"), "deadline"),
        (("15 December 2026, 11:59 pm ET", "15 December 2026, 11:59 PM ET"), "deadline"),
        (("7 January 2027", "January 7, 2027"), "deadline"),
        (("- Booth MiF:", "- Chicago Booth:"), "Booth MiF"),
        (("Corporate Finance (FIN-101)", "Corporate Finance"), "course"),
        (("Dear Professor Jansen,", "Dear Professor,"), "surname"),
    ],
)
def test_missing_or_changed_facts_are_reported(
    others: list[str], change: tuple[str, str], fragment: str
) -> None:
    body = _body().replace(*change)
    problems = fact_problems(body, PAYLOAD, others)
    assert problems and any(fragment in p for p in problems), problems


@pytest.mark.parametrize(
    "mention",
    [
        "I also apply to Columbia.",
        "MIT is on my list.",
        "the Columbia MSFE programme",
        "Columbia's programme",
    ],
)
def test_unselected_programme_mentions_are_reported(others: list[str], mention: str) -> None:
    assert fact_problems(_body(extra=mention), PAYLOAD, others)


@pytest.mark.parametrize("clean", ["a phone booth", "I will SUBMIT it", "the columbia river"])
def test_lower_case_words_and_parts_of_words_are_not_mentions(others: list[str], clean: str) -> None:
    assert fact_problems(_body(extra=clean), PAYLOAD, others) == []


@pytest.mark.parametrize(("words", "ok"), [(179, False), (180, True), (280, True), (281, False)])
def test_word_limits(words: int, ok: bool) -> None:
    assert (form_problems(_body(words), PAYLOAD, []) == []) is ok


@pytest.mark.parametrize(
    "salutation",
    ["Dear Prof. Jansen,", "Dear Professor Jansen", "Dear professor Jansen,", "Dear Dr. Jansen,"],
)
def test_salutation_must_be_exact(salutation: str) -> None:
    problems = form_problems(_body(salutation=salutation), PAYLOAD, [])
    assert any("salutation" in p.lower() for p in problems), problems


@pytest.mark.parametrize(
    ("sentence", "ok"),
    [
        ("I expect to graduate cum laude in June 2027.", True),
        ("I am on track to graduate cum laude.", True),
        ("My anticipated cum laude reflects steady work.", True),
        ("I graduated cum laude.", False),
        ("I will graduate Cum Laude.", False),
        ("I expect a strong finish. I graduated cum laude.", False),  # the claim's own sentence decides
    ],
)
def test_anticipated_achievements_must_be_marked_as_expected(sentence: str, ok: bool) -> None:
    problems = form_problems(_body(extra=sentence), PAYLOAD, ["cum laude"])
    assert (problems == []) is ok, problems


def test_anticipated_terms_come_from_anticipated_entries() -> None:
    entries = [
        BiographyEntry(id="cl", category="honour", title="Cum laude (expected)", state="anticipated"),
        BiographyEntry(id="gmat", category="test", title="GMAT 735", state="actual"),
    ]
    assert anticipated_terms(entries) == ["Cum laude"]


def test_subject_must_match_the_payload() -> None:
    assert subject_matches("Request for a letter of recommendation: master's programmes in finance", PAYLOAD)
    assert not subject_matches("Request for a letter of recommendation", PAYLOAD)


@pytest.mark.parametrize(
    ("sentence", "ok"),
    [
        ("I was a research assistant from January to June 2026.", True),
        ("My exchange was in May 2026.", True),  # May has no short form
        ("I was a research assistant from Jan–Jun 2026.", False),
        ("I started in Sept. 2025.", False),
        ("The exam was on 3 Dec 2025.", False),
        ("A decent December.", True),  # only whole words count
    ],
)
def test_months_are_spelled_out(sentence: str, ok: bool) -> None:
    """DECISIONS #49: biography dates are written with spelled-out months in the email."""
    problems = form_problems(_body(extra=sentence), PAYLOAD, [])
    assert (problems == []) is ok, problems


def test_a_first_name_that_looks_like_a_month_is_not_flagged() -> None:
    """Jan is a common Dutch first name: "Jan Peters" in the email is a name, not a month."""
    payload = {**PAYLOAD, "professor": {**PAYLOAD["professor"], "name": "Prof. Jan Peters"}}
    assert form_problems(_body(extra="Thank you, Jan Peters, for the course."), payload, []) == []
    assert form_problems(_body(extra="It ran from Jan to June."), payload, []) == []  # can't tell; allowed


def test_each_deadline_must_stand_with_its_own_programme(others: list[str]) -> None:
    """docs/04 §4: a programme's short_name *and its* deadline_text; swapped deadlines are a fact failure."""
    body = _body()
    swapped = body.replace(
        "- Princeton MFin: 15 December 2026, 11:59 pm ET", "- Princeton MFin: 7 January 2027"
    ).replace("- Booth MiF: 7 January 2027", "- Booth MiF: 15 December 2026, 11:59 pm ET")
    problems = fact_problems(swapped, PAYLOAD, others)
    assert any("Princeton MFin" in p and "deadline" in p for p in problems), problems
    assert fact_problems(body, PAYLOAD, others) == []


def test_one_warning_per_mention_not_per_overlapping_name(others: list[str]) -> None:
    """ui-reviewer: "MIT Sloan MFin" used to give four warnings (MIT Sloan MFin, MIT, Sloan, MIT Sloan)."""
    problems = fact_problems(_body(extra="I also apply to the MIT Sloan MFin."), PAYLOAD, others)
    assert problems == ["Mentions MIT Sloan MFin, which is not one of the programmes in this request"]


def test_app_warnings_give_one_line_per_problem(others: list[str]) -> None:
    """ui re-review: a wrong salutation or an unnamed programme was listed twice under "Check before sending".
    The grader's per-criterion counts (fact_problems, form_problems) are unchanged."""
    from hub.ai.email_checks import app_warnings

    body = _body(salutation="Dear Professor,").replace("- Booth MiF: 7 January 2027\n", "")
    warnings = app_warnings(PAYLOAD["constraints"]["subject"], body, PAYLOAD, others, [])
    assert sum("alutation" in w for w in warnings) == 1, warnings
    assert sum("Booth MiF" in w for w in warnings) == 1, warnings
    assert fact_problems(body, PAYLOAD, others) and form_problems(body, PAYLOAD, [])  # grader unchanged
