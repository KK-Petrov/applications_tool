"""C-2a-02: letter coverage counts and next actions match docs/02 §4 on the fixture set.

Fixture: tests/fixtures/data (four programmes) plus tests/fixtures/letters (the invented programme
``early-msf``, deadline 10 Nov 2026, and one invented professor per priority row). Today is Friday
30 October 2026 (helpers.FIXED_NOW, New York).

Request-due dates (primary deadline's local date − 42 days): early-msf 29 Sep 2026, Princeton 3 Nov,
MIT 24 Nov, Booth 26 Nov, Columbia 4 Dec. Submission checks start 14 days before the deadline.
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest

from hub.config import Config, load_config
from hub.models import Professor, Programme
from hub.rules import letters as L
from hub.store import Store

from helpers import FIXED_NOW
from paths import FIXTURE_DATA, FIXTURES

TODAY = date(2026, 10, 30)


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> tuple[Config, dict[str, Programme], list[Professor]]:
    """The fixture data with the letters fixture's programme and professors (read-only, so shared)."""
    folder = tmp_path_factory.mktemp("letters") / "data"
    shutil.copytree(FIXTURE_DATA, folder)
    shutil.rmtree(folder / "professors")
    shutil.copytree(FIXTURES / "letters" / "professors", folder / "professors")
    shutil.copy(FIXTURES / "letters" / "programmes" / "early-msf.yaml", folder / "programmes")
    config = load_config(env={"HUB_DATA_DIR": str(folder)})
    snapshot = Store.from_config(config).load_all()
    assert snapshot.errors == []
    return config, {p.id: p for p in snapshot.programmes}, snapshot.professors


def _action(world, pid: str) -> L.NextAction | None:
    config, programmes, professors = world
    prof = next(p for p in professors if p.id == pid)
    return L.next_action(prof, programmes, TODAY, FIXED_NOW, config)


# ---- next actions: one row per priority in docs/02 §4 -------------------------------------------

EXPECTED = [
    # professor, priority, text, urgent, programme, date
    ("lind", 1, "Add course details", False, None, None),
    ("okafor", 2, "Send request (overdue since 29 Sep 2026)", True, "early-msf", date(2026, 9, 29)),
    ("brandt", 3, "Follow up (asked 20 Oct 2026)", True, "princeton-mfin", date(2026, 10, 27)),
    ("castillo", 4, "Send request by 26 Nov 2026", False, "booth-mif", date(2026, 11, 26)),
    ("duval", 4, "Send request by 3 Nov 2026", True, "princeton-mfin", date(2026, 11, 3)),
    ("ferreira", 4, "Send request by 24 Nov 2026", False, "mit-sloan-mfin", date(2026, 11, 24)),
    ("ishikawa", 5, "Check submission for Early MSF", True, "early-msf", date(2026, 10, 27)),
    ("kowalski", 6, "Send thank-you note", False, "early-msf", date(2026, 10, 25)),
    ("moreau", 7, "Waiting for reply", False, "early-msf", date(2026, 11, 4)),
    ("novak", 8, "Waiting for submission", False, "booth-mif", date(2026, 12, 24)),
]


@pytest.mark.parametrize(
    ("pid", "priority", "text", "urgent", "programme", "on"), EXPECTED, ids=lambda v: str(v)
)
def test_next_action_matches_the_priority_table(world, pid, priority, text, urgent, programme, on) -> None:
    action = _action(world, pid)
    assert action is not None
    assert (action.priority, action.text, action.urgent, action.programme_id, action.on) == (
        priority,
        text,
        urgent,
        programme,
        on,
    )


@pytest.mark.parametrize("pid", ["quinn", "rossi", "santos"])
def test_no_action_for_only_declined_no_letters_or_all_done(world, pid: str) -> None:
    assert _action(world, pid) is None


def test_every_priority_row_is_covered_by_the_fixture() -> None:
    assert {row[1] for row in EXPECTED} == set(range(1, 9))


def test_the_two_examples_named_in_docs_02(world) -> None:
    """docs/02 §4: Princeton requested_on 2026-10-20 → 3; Booth not_asked (due 26 Nov) → 4, not urgent."""
    assert _action(world, "brandt").priority == 3
    castillo = _action(world, "castillo")
    assert (castillo.priority, castillo.urgent) == (4, False)


def test_request_soon_window_comes_from_config(world) -> None:
    config, programmes, professors = world
    duval = next(p for p in professors if p.id == "duval")  # Princeton due 3 Nov: 4 days away
    letters = config.letters.model_copy(update={"request_soon_days": 3})
    narrow = config.model_copy(update={"letters": letters})
    assert L.next_action(duval, programmes, TODAY, FIXED_NOW, narrow).urgent is False
    assert L.next_action(duval, programmes, TODAY, FIXED_NOW, config).urgent is True


def test_follow_up_starts_exactly_after_follow_up_days(world) -> None:
    config, programmes, professors = world
    brandt = next(p for p in professors if p.id == "brandt")  # asked 20 Oct; follow up from 27 Oct
    assert L.next_action(brandt, programmes, date(2026, 10, 26), FIXED_NOW, config).priority == 7
    assert L.next_action(brandt, programmes, date(2026, 10, 27), FIXED_NOW, config).priority == 3


def test_overdue_starts_the_day_after_request_due(world) -> None:
    config, programmes, professors = world
    duval = next(p for p in professors if p.id == "duval")  # Princeton request due 3 Nov
    assert L.next_action(duval, programmes, date(2026, 11, 3), FIXED_NOW, config).priority == 4
    after = L.next_action(duval, programmes, date(2026, 11, 4), FIXED_NOW, config)
    assert (after.priority, after.text) == (2, "Send request (overdue since 3 Nov 2026)")


def test_letters_to_an_unknown_programme_are_ignored(world) -> None:
    config, programmes, professors = world
    castillo = next(p for p in professors if p.id == "castillo")
    without_booth = {k: v for k, v in programmes.items() if k != "booth-mif"}
    assert L.next_action(castillo, without_booth, TODAY, FIXED_NOW, config) is None


# ---- filter buckets ------------------------------------------------------------------------------

BUCKETS = {
    "lind": L.Bucket.NEEDS_ACTION,
    "okafor": L.Bucket.NEEDS_ACTION,
    "brandt": L.Bucket.NEEDS_ACTION,
    "castillo": L.Bucket.NEEDS_ACTION,
    "duval": L.Bucket.NEEDS_ACTION,
    "ferreira": L.Bucket.NEEDS_ACTION,
    "ishikawa": L.Bucket.NEEDS_ACTION,
    "kowalski": L.Bucket.NEEDS_ACTION,
    "moreau": L.Bucket.WAITING,
    "novak": L.Bucket.WAITING,
    "quinn": L.Bucket.DONE,
    "santos": L.Bucket.DONE,
    "rossi": None,  # no letters: in no filter (shown under "All")
}


@pytest.mark.parametrize(("pid", "expected"), BUCKETS.items())
def test_filter_bucket(world, pid: str, expected) -> None:
    config, programmes, professors = world
    prof = next(p for p in professors if p.id == pid)
    assert L.bucket(prof, L.next_action(prof, programmes, TODAY, FIXED_NOW, config)) == expected


# ---- coverage per programme ----------------------------------------------------------------------

COVERAGE = [
    # programme, required, assumed, confirmed, requested, in_play, over_ask
    ("early-msf", 1, False, 2, 1, 3, True),
    ("princeton-mfin", 3, False, 0, 2, 2, False),
    ("booth-mif", 2, True, 2, 0, 2, False),
    ("mit-sloan-mfin", 2, True, 1, 0, 1, False),
    ("columbia-msfe", 2, False, 0, 0, 0, False),
]


@pytest.mark.parametrize(("pid", "req", "assumed", "conf", "reqd", "play", "over"), COVERAGE)
def test_coverage_counts(world, pid, req, assumed, conf, reqd, play, over) -> None:
    config, programmes, professors = world
    p = programmes[pid]
    assert L.required(p, config.letters.default_required) == L.Required(req, assumed)
    assert L.confirmed(pid, professors) == conf
    assert L.requested(pid, professors) == reqd
    assert L.in_play(pid, professors) == play
    assert L.over_ask(p, professors, config) is over


def test_request_due_dates(world) -> None:
    config, programmes, _ = world
    days = config.reminders.letters_request_days
    due = {pid: L.request_due(p, FIXED_NOW, days) for pid, p in programmes.items()}
    assert due == {
        "early-msf": date(2026, 9, 29),
        "princeton-mfin": date(2026, 11, 3),
        "mit-sloan-mfin": date(2026, 11, 24),
        "booth-mif": date(2026, 11, 26),
        "columbia-msfe": date(2026, 12, 4),
    }


def test_fixture_professors_are_invented() -> None:
    for path in (FIXTURES / "letters" / "professors").glob("*.yaml"):
        assert "@example.org" in Path(path).read_text(encoding="utf-8")


# ---- DECISIONS #43 (L1): re-entering Requested keeps the original request date ---------------------


def test_reentering_requested_keeps_the_original_requested_on() -> None:
    from hub.models.professor import Letter, LetterStatus

    asked = L.set_status(None, LetterStatus.REQUESTED, date(2026, 11, 1))
    confirmed = L.set_status(asked, LetterStatus.CONFIRMED, date(2026, 11, 3))
    again = L.set_status(confirmed, LetterStatus.REQUESTED, date(2026, 11, 5))
    assert again.status == LetterStatus.REQUESTED
    assert again.requested_on == date(2026, 11, 1)  # follow-ups count from the stored date
    assert again.confirmed_on == date(2026, 11, 3)  # history is kept
    assert again.last_contact == date(2026, 11, 3)  # nothing new was stamped
    first = L.set_status(Letter(status=LetterStatus.DRAFT_READY), LetterStatus.REQUESTED, date(2026, 11, 5))
    assert first.requested_on == date(2026, 11, 5) and first.last_contact == date(2026, 11, 5)
