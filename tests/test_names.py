"""Names (docs/02 §9; DECISIONS #44): title tokens, initials, Dutch particles, the salutation and matching.

Parsed in Python from the name as written, with ``config.names`` (particles, title tokens, title precedence).
"""

from __future__ import annotations

import pytest

from hub.config import load_config
from hub.rules import names

CFG = load_config().names


def _p(name: str) -> names.ParsedName:
    return names.parse(name, CFG)


# ---- the worked examples of docs/02 §9 ----------------------------------------------------------------


def test_worked_example_professor_with_particles() -> None:
    p = _p("Prof. dr. A. van den Berg")
    assert (p.title, p.surname, p.initials) == ("Professor", "van den Berg", ("A",))
    assert p.salutation == "Van den Berg"
    assert f"Dear {p.title} {p.salutation}," == "Dear Professor Van den Berg,"


def test_worked_example_doctor_with_particle() -> None:
    p = _p("Dr. J. de Vries")
    assert (p.title, p.surname, p.initials) == ("Dr.", "de Vries", ("J",))
    assert f"Dear {p.title} {p.salutation}," == "Dear Dr. De Vries,"


def test_worked_example_no_title_means_professor() -> None:
    p = _p("S. Whitfield")
    assert (p.title, p.surname, p.initials, p.title_found) == ("Professor", "Whitfield", ("S",), False)


# ---- title tokens -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "title"),
    [
        ("Prof. dr. A.B. van den Berg", "Professor"),  # highest-ranked token wins
        ("dr. prof. A. Smit", "Professor"),  # order of the tokens doesn't matter
        ("PROF A. Smit", "Professor"),  # case-insensitive, with or without a full stop
        ("Prof.dr.ir. K. Bakker", "Professor"),  # joined tokens
        ("Dr. ir. K. Bakker", "Dr."),
        ("ir. K. Bakker", "Ir."),
        ("drs. P. Jansen", "Drs."),
        ("mr. L. de Groot", "Mr."),
        ("Professor Maria Lopez", "Professor"),
        ("Maria Lopez", "Professor"),  # no token: the default
    ],
)
def test_title_is_prefilled_from_the_highest_ranked_token(name: str, title: str) -> None:
    assert _p(name).title == title


def test_title_tokens_are_stripped_from_the_name() -> None:
    p = _p("Prof.dr.ir. K. Bakker")
    assert (p.surname, p.initials, p.first_names, p.title_found) == ("Bakker", ("K",), (), True)


# ---- initials and first names -------------------------------------------------------------------------


def test_joined_initials() -> None:
    p = _p("Prof. dr. J.A.M. de Groot")
    assert p.initials == ("J", "A", "M") and p.surname == "de Groot"


def test_initials_are_derived_from_first_names() -> None:
    p = _p("Jan Willem de Vries")
    assert p.first_names == ("Jan", "Willem") and p.initials == ("J", "W") and p.surname == "de Vries"


def test_a_name_without_particles_takes_the_last_token_as_surname() -> None:
    p = _p("Dr. Sam Okafor-Smith")
    assert (p.surname, p.first_names, p.initials) == ("Okafor-Smith", ("Sam",), ("S",))


# ---- Dutch particles ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "surname", "salutation"),
    [
        ("A. van den Berg", "van den Berg", "Van den Berg"),
        ("A. van der Meer", "van der Meer", "Van der Meer"),
        ("B. ter Horst", "ter Horst", "Ter Horst"),
        ("C. in 't Veld", "in 't Veld", "In 't Veld"),
        ("D. op de Beek", "op de Beek", "Op de Beek"),
        ("E. von Neumann", "von Neumann", "Von Neumann"),
        ("F. 't Hart", "'t Hart", "'t Hart"),  # an apostrophe stays lower case: nothing to capitalise
        ("Van den Berg", "Van den Berg", "Van den Berg"),  # standing alone as written
    ],
)
def test_particles_belong_to_the_surname(name: str, surname: str, salutation: str) -> None:
    p = _p(name)
    assert (p.surname, p.salutation) == (surname, salutation)


# ---- matching (lecturer ↔ existing professor, Add professor suggestions) ------------------------------


def test_matching_ignores_case_and_diacritics() -> None:
    assert names.match(_p("Prof. dr. K. Müller"), _p("K. Muller")) == "exact"
    assert names.match(_p("A. VAN DEN BERG"), _p("Prof. dr. A. van den Berg")) == "exact"
    assert names.match(_p("Dr. Ł. Żak"), _p("L. Zak")) == "exact"


def test_matching_needs_the_same_initials() -> None:
    assert names.match(_p("A. de Vries"), _p("J. de Vries")) is None
    assert names.match(_p("A. de Vries"), _p("A. Vries")) is None  # the particle is part of the surname


def test_a_name_without_initials_matches_on_surname_only() -> None:
    assert names.match(_p("Prof. Smit"), _p("J. Smit")) == "surname"
    assert names.match(_p("Smit"), _p("Smit")) == "surname"
    assert names.match(_p("Prof. Smit"), _p("J. Jansen")) is None


def test_different_written_first_names_are_never_an_exact_match() -> None:
    """data-integrity Phase 3 r1 F2 (DECISIONS #105, D1 a): initials derived from first names made "Lise" and
    "Lars" the same person; with first names written on both sides they must agree, else surname only."""
    assert names.match(_p("Lise Ødegaard"), _p("Prof. Lars Ødegaard")) == "surname"
    assert names.match(_p("Prof. Eva Jansen"), _p("Prof. Dr. Eva Jansen")) == "exact"
    assert names.match(_p("EVA JANSEN"), _p("Eva Jänsen")) == "exact"  # case and diacritics still ignored
    assert names.match(_p("Jan de Vries"), _p("J. de Vries")) == "exact"  # initials only on one side (#64)
    assert names.match(_p("Lise Ødegaard"), _p("Dr. K. Ødegaard")) is None  # initials differ


def test_the_matching_key_is_surname_plus_initials() -> None:
    assert _p("Prof. dr. A. van den Berg").key == ("van den berg", ("a",))
    assert _p("Jan de Vries").key == _p("J. de Vries").key


def test_surname_match_for_suggestions_is_case_and_diacritic_insensitive() -> None:
    assert names.same_surname("Müller", "MULLER")
    assert names.same_surname("van den Berg", "Van Den Berg")
    assert not names.same_surname("Berg", "van den Berg")
