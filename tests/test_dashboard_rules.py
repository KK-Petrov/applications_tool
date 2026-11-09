"""Programme rules behind the dashboard (docs/01 §3, docs/02 §3–4) and the interim alert strip."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from hub.models.common import Verification
from hub.models.programme import ProgrammeStatus
from hub.rules import alerts, letters
from hub.rules.alerts import AlertKind
from hub.rules.programmes import (
    DEADLINE_CHANGED_REASON,
    NOT_CHECKED_REASON,
    badge,
    dashboard_order,
    documents_progress,
    primary_deadline,
    requirements_progress,
)
from hub.store import Store

NOW = datetime(2026, 10, 30, 16, 0, tzinfo=UTC)  # 30 Oct 2026, 12:00 New York


@pytest.fixture
def snapshot(config):
    snap = Store.from_config(config).load_all()
    assert not snap.errors
    return snap


def _get(snapshot, pid: str):
    return next(p for p in snapshot.programmes if p.id == pid)


def _with_two_deadlines(programme):
    first = programme.deadlines[0]
    second = first.model_copy(update={"id": "round-2", "due_local": datetime(2027, 2, 1, 17, 0)})
    return programme.model_copy(update={"deadlines": [first, second]})


def test_primary_deadline_is_earliest_not_passed(snapshot) -> None:
    princeton = _with_two_deadlines(_get(snapshot, "princeton-mfin"))
    assert primary_deadline(princeton, NOW).id == "main"
    after_main = datetime(2026, 12, 16, 5, 0, tzinfo=UTC)  # one minute after 23:59 New York
    assert primary_deadline(princeton, after_main).id == "round-2"


def test_primary_deadline_uses_the_instant_not_the_date(snapshot) -> None:
    princeton = _with_two_deadlines(_get(snapshot, "princeton-mfin"))
    just_before = datetime(2026, 12, 16, 4, 58, tzinfo=UTC)  # 23:58 New York
    assert primary_deadline(princeton, just_before).id == "main"


def test_primary_deadline_when_all_passed_is_the_last(snapshot) -> None:
    princeton = _with_two_deadlines(_get(snapshot, "princeton-mfin"))
    assert primary_deadline(princeton, datetime(2027, 6, 1, tzinfo=UTC)).id == "round-2"


def test_primary_deadline_ignores_list_order(snapshot) -> None:
    princeton = _with_two_deadlines(_get(snapshot, "princeton-mfin"))
    reversed_ = princeton.model_copy(update={"deadlines": list(reversed(princeton.deadlines))})
    assert primary_deadline(reversed_, NOW).id == "main"


def test_dashboard_order_by_deadline(snapshot) -> None:
    order = [p.id for p in dashboard_order(snapshot.programmes, NOW)]
    assert order == ["princeton-mfin", "mit-sloan-mfin", "booth-mif", "columbia-msfe"]


def test_dashboard_order_puts_passed_last(snapshot) -> None:
    later = datetime(2026, 12, 20, tzinfo=UTC)
    order = [p.id for p in dashboard_order(snapshot.programmes, later)]
    assert order == ["mit-sloan-mfin", "booth-mif", "columbia-msfe", "princeton-mfin"]


def test_progress_counts(snapshot) -> None:
    princeton = _get(snapshot, "princeton-mfin")
    assert requirements_progress(princeton) == (0, 9)
    docs = princeton.documents
    docs[0].status = "received"
    docs[1].status = "waived"
    docs[2].status = "sent"  # sent is not received
    assert documents_progress(princeton) == (2, 4)
    princeton.requirements[0].done = True
    assert requirements_progress(princeton) == (1, 9)


def test_badge_uses_the_verification_note(snapshot) -> None:
    assert badge(_get(snapshot, "princeton-mfin")).reason == "Last cycle's date"
    assert (
        badge(_get(snapshot, "columbia-msfe")).reason == "Researched, not checked"
    )  # official source since 5 Nov 2026
    assert badge(_get(snapshot, "booth-mif")).unverified


def test_badge_without_note(snapshot) -> None:
    p = _get(snapshot, "booth-mif").model_copy(update={"verification_note": None})
    assert badge(p).unverified and badge(p).reason == NOT_CHECKED_REASON


def test_badge_verified_programme_with_an_unverified_deadline(snapshot) -> None:
    p = _get(snapshot, "princeton-mfin").model_copy(
        update={
            "verification": Verification.VERIFIED,
            "verification_note": None,
            "verified_on": date(2026, 11, 1),
        }
    )
    assert badge(p).unverified and badge(p).reason == DEADLINE_CHANGED_REASON
    for d in p.deadlines:
        d.verification = Verification.VERIFIED
    assert not badge(p).unverified and badge(p).reason is None


def test_letters_required_and_assumed(snapshot, config) -> None:
    default = config.letters.default_required
    assert letters.required(_get(snapshot, "princeton-mfin"), default) == letters.Required(3, False)
    assert letters.required(_get(snapshot, "booth-mif"), default) == letters.Required(2, True)


def test_letters_counts(snapshot) -> None:
    # fixtures: jansen has princeton draft_ready and booth requested; whitfield has no letters
    assert letters.confirmed("princeton-mfin", snapshot.professors) == 0
    assert letters.requested("booth-mif", snapshot.professors) == 1
    jansen = next(p for p in snapshot.professors if p.id == "jansen")
    jansen.letters["booth-mif"].status = "confirmed"
    jansen.letters["princeton-mfin"].status = "submitted"
    assert letters.confirmed("booth-mif", snapshot.professors) == 1
    assert letters.confirmed("princeton-mfin", snapshot.professors) == 1
    assert letters.requested("booth-mif", snapshot.professors) == 0


def test_request_due_is_school_local_date_minus_42(snapshot, config) -> None:
    days = config.reminders.letters_request_days
    assert letters.request_due(_get(snapshot, "princeton-mfin"), NOW, days) == date(2026, 11, 3)
    assert letters.request_due(_get(snapshot, "booth-mif"), NOW, days) == date(2026, 11, 26)


def test_alert_is_princeton_letter_request(snapshot, config) -> None:
    alert = alerts.most_urgent(snapshot.programmes, snapshot.professors, NOW, config)
    assert alert is not None
    assert (alert.kind, alert.programme_id, alert.due) == (
        AlertKind.LETTERS,
        "princeton-mfin",
        date(2026, 11, 3),
    )
    assert not alert.overdue
    assert (alert.letters_required, alert.letters_confirmed, alert.letters_assumed) == (3, 0, False)


def test_alert_overdue_after_request_due(snapshot, config) -> None:
    alert = alerts.most_urgent(
        snapshot.programmes, snapshot.professors, datetime(2026, 11, 5, 15, tzinfo=UTC), config
    )
    assert alert.programme_id == "princeton-mfin" and alert.overdue


def test_alert_skips_covered_and_submitted_programmes(snapshot, config) -> None:
    """Rewritten for the reminder engine (Phase 3, DECISIONS #106 Q3). Same scenario; changed expectation: the
    interim alert said MIT's letter request (24 Nov); the engine's earliest task is the follow-up of Jansen's
    Booth request (asked 2 Nov, follow up 9 Nov)."""
    princeton = _get(snapshot, "princeton-mfin")
    princeton.status = ProgrammeStatus.SUBMITTED
    alert = alerts.most_urgent(snapshot.programmes, snapshot.professors, NOW, config)
    assert alert.programme_id != "princeton-mfin"
    assert (alert.kind, alert.reminder.rule, alert.programme_id, alert.due) == (
        AlertKind.TASK,
        "letter_follow_up",
        "booth-mif",
        date(2026, 11, 9),
    )


def test_alert_falls_back_to_the_deadline_when_letters_are_covered(snapshot, config) -> None:
    """Rewritten for the reminder engine (DECISIONS #106 Q3). Same scenario (no programme needs letters);
    changed expectation: the interim alert fell back to Princeton's deadline; the engine names the earliest
    task first, the Booth follow-up (9 Nov). With no task left, the deadline comes back (next test)."""
    for p in snapshot.programmes:
        p.letters_required = 0
    alert = alerts.most_urgent(snapshot.programmes, snapshot.professors, NOW, config)
    assert (alert.kind, alert.reminder.rule, alert.programme_id, alert.due) == (
        AlertKind.TASK,
        "letter_follow_up",
        "booth-mif",
        date(2026, 11, 9),
    )


def test_alert_is_the_deadline_when_no_task_comes_before_it(snapshot, config) -> None:
    for p in snapshot.programmes:
        p.letters_required, p.documents, p.essays = 0, [], []
    for prof in snapshot.professors:
        prof.letters = {}
    alert = alerts.most_urgent(snapshot.programmes, snapshot.professors, NOW, config)
    assert (alert.kind, alert.programme_id, alert.due) == (
        AlertKind.DEADLINE,
        "princeton-mfin",
        date(2026, 12, 15),
    )


def test_no_alert_when_everything_is_submitted(snapshot, config) -> None:
    """Same scenario and expectation as before. Only Princeton's scheduled math test remains; the strip names
    the next thing to do (docs/03 §4.1), so a "deadline in N days" notice or an interview is in Upcoming
    reminders, never the alert (DECISIONS #106 Q3)."""
    for p in snapshot.programmes:
        p.status = ProgrammeStatus.SUBMITTED  # only Princeton's scheduled math test remains
    assert alerts.most_urgent(snapshot.programmes, snapshot.professors, NOW, config) is None
