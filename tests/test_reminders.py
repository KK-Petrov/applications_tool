"""The reminder engine (docs/02 §5; C-3-01).

The worked example (fixture tests/fixtures/reminders/, today 2026-10-30, reminder_tz America/New_York)
gives exactly the 30 rows of tests/fixtures/reminders/golden.csv (hand-derived from the docs/02 §5.4 table),
plus the three extra cases of §5.4, event shapes (§5.2), IDs and titles (§5.3), and the Phase 3 plan
decisions (DECISIONS #106): overdue tasks, unverified titles, the reminder zone, documents from "Add standard
steps and documents".
"""

from __future__ import annotations

import csv
import shutil
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from hub.config import load_config
from hub.models.common import Verification
from hub.models.professor import Letter, LetterStatus, Professor
from hub.models.programme import InterviewStatus, ProgrammeStatus
from hub.rules import reminders as engine
from hub.store import Snapshot, Store

FIXTURE = Path(__file__).parent / "fixtures" / "reminders"
TODAY = date(2026, 10, 30)


@pytest.fixture
def snap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Snapshot:
    target = tmp_path / "data"
    shutil.copytree(FIXTURE, target)
    monkeypatch.setenv("HUB_DATA_DIR", str(target))
    snapshot = Store.from_config(load_config()).load_all()
    assert not snapshot.errors
    return snapshot


def _run(snap: Snapshot, today: date = TODAY) -> list[engine.Reminder]:
    return engine.reminders(snap.programmes, snap.professors, snap.settings.reminder_tz, load_config(), today)


def _key(r: engine.Reminder) -> tuple[str, str, str, str]:
    when = f"{r.start:%Y-%m-%d %H:%M} {r.tz}" if r.timed_at_own_instant else r.date.isoformat()
    return r.rule, r.programme_id, r.subject_id or "", when


def _golden() -> dict[int, tuple[str, str, str, str]]:
    with (FIXTURE / "golden.csv").open(encoding="utf-8", newline="") as fh:
        return {
            int(row["row"]): (row["rule"], row["programme"], row["subject"], row["when"])
            for row in csv.DictReader(fh)
        }


def _without(*rows: int) -> set[tuple[str, str, str, str]]:
    return {v for k, v in _golden().items() if k not in rows}


def _prog(snap: Snapshot, pid: str):  # noqa: ANN202
    return next(p for p in snap.programmes if p.id == pid)


# ---- C-3-01: the golden table and the extra cases ---------------------------------------------------


def test_the_worked_example_gives_exactly_the_30_golden_rows(snap: Snapshot) -> None:
    got = [_key(r) for r in _run(snap)]
    assert len(got) == 30 and len(set(got)) == 30
    assert set(got) == set(_golden().values())


def test_all_princeton_letters_submitted_removes_rows_1_and_3(snap: Snapshot) -> None:
    """Three submitted letters cover Princeton: rows 1 (request) and 3 (check) go. Row 30, the follow-up, also
    goes, because letter_follow_up fires only while the letter is still ``requested`` (§5.1)."""
    done = Letter(
        status=LetterStatus.SUBMITTED, requested_on=date(2026, 10, 29), submitted_on=date(2026, 10, 30)
    )
    snap.professors[0].letters["princeton-mfin"] = done
    for n in ("b", "c"):
        snap.professors.append(
            Professor(
                id=f"example-{n}",
                name=f"Prof. {n.upper()}. Example",
                university="X",
                letters={"princeton-mfin": done},
            )
        )
    assert {_key(r) for r in _run(snap)} == _without(1, 3, 30)


def test_princeton_submitted_keeps_only_its_interview_rows(snap: Snapshot) -> None:
    """Rows 1–9 go and rows 10–11 stay; row 30 (Princeton's follow-up) goes too: "all its reminders disappear
    except interviews" (§5.1)."""
    _prog(snap, "princeton-mfin").status = ProgrammeStatus.SUBMITTED
    assert {_key(r) for r in _run(snap)} == _without(*range(1, 10), 30)


def test_a_reminder_dated_before_today_is_not_produced(snap: Snapshot) -> None:
    assert {_key(r) for r in _run(snap, date(2026, 11, 4))} == _without(1)


# ---- event shapes, IDs and titles (§5.2, §5.3) ---------------------------------------------------------


def test_titles_ids_and_descriptions_of_the_worked_example(snap: Snapshot) -> None:
    by_id = {r.id: r for r in _run(snap)}
    first = by_id["letters_request_due:princeton-mfin"]
    assert first.title == "[Hub] Princeton MFin: request letters (3 needed)"
    assert "http://127.0.0.1:8765/programmes/princeton-mfin" in first.description
    assert "15 Dec 2026, 23:59 (America/New_York)" in first.description  # time, zone and source (rule 10)
    assert "Source: https://bcf.princeton.edu/" in first.description
    assert (
        by_id["letters_request_due:booth-mif"].title == "[Hub] Booth MiF: request letters (2 needed, assumed)"
    )
    assert (
        by_id["letter_follow_up:princeton-mfin:example-a"].title
        == "[Hub] Princeton MFin: follow up with Prof. A. Example"
    )
    assert by_id["interview:princeton-mfin:math-test"].title == "[Hub] Princeton MFin: Math test (estimated)"
    assert (
        by_id["interview_eve:princeton-mfin:math-test"].title
        == "[Hub] Princeton MFin: Math test tomorrow (estimated)"
    )
    assert len(by_id) == 30  # ids are unique


def test_event_shapes(snap: Snapshot) -> None:
    by_id = {r.id: r for r in _run(snap)}
    rule = by_id["letters_request_due:princeton-mfin"]
    assert rule.start == datetime(2026, 11, 3, 9, 0, tzinfo=ZoneInfo("America/New_York"))
    assert rule.start.astimezone(UTC).hour == 14  # 09:00 EST after the 1 Nov changeover
    assert (rule.minutes, rule.popups) == (15, (0,))
    deadline = by_id["deadline:princeton-mfin"]
    assert deadline.start == datetime(2026, 12, 15, 23, 59, tzinfo=ZoneInfo("America/New_York"))
    assert (deadline.minutes, deadline.popups) == (30, (1440, 120))
    booth = by_id["deadline:booth-mif"]
    assert booth.start.astimezone(UTC) == datetime(2027, 1, 8, 5, 59, tzinfo=UTC)  # 23:59 Chicago
    interview = by_id["interview:princeton-mfin:math-test"]
    assert (interview.minutes, interview.popups) == (60, (1440, 60))


def test_rule_events_follow_the_reminder_zone_and_keep_their_dates(snap: Snapshot) -> None:
    """Back in Rotterdam (DECISIONS #106 addition 2): rule events at 09:00 Amsterdam on the same dates; the
    deadline and the interview stay at their own instant in their own zone."""
    before = {r.id: r for r in _run(snap)}
    snap.settings.reminder_tz = "Europe/Amsterdam"
    after = {r.id: r for r in _run(snap)}
    assert before.keys() == after.keys()
    rule = after["documents_due:princeton-mfin"]
    assert rule.start == datetime(2026, 11, 24, 9, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))
    assert rule.date == before["documents_due:princeton-mfin"].date
    assert after["deadline:princeton-mfin"].start == before["deadline:princeton-mfin"].start


# ---- conditions ------------------------------------------------------------------------------------


def test_declined_letters_do_not_count_for_the_letters_check(snap: Snapshot) -> None:
    columbia = "columbia-msfe"
    submitted = Letter(status=LetterStatus.SUBMITTED, submitted_on=date(2026, 10, 20))
    declined = Letter(status=LetterStatus.DECLINED)
    for n, letter in (("b", submitted), ("c", submitted), ("d", declined)):
        snap.professors.append(
            Professor(
                id=f"example-{n}", name=f"Dr. {n.upper()}. X", university="X", letters={columbia: letter}
            )
        )
    ids = {r.id for r in _run(snap)}
    assert f"letters_check:{columbia}" not in ids and f"letters_request_due:{columbia}" not in ids


def test_an_interview_that_is_not_scheduled_has_no_reminders(snap: Snapshot) -> None:
    _prog(snap, "princeton-mfin").interviews[0].status = InterviewStatus.INVITED
    assert not [r for r in _run(snap) if r.category == "interview"]


def test_documents_added_by_standard_items_make_documents_due_fire(snap: Snapshot) -> None:
    """DECISIONS #106 addition 3: documents added by "Add standard steps and documents" (#97)."""
    from hub import standard_items
    from hub.models.programme import Requirement

    cfg = load_config()
    columbia = _prog(snap, "columbia-msfe")
    columbia.documents = []
    columbia.requirements = [Requirement(id="transcripts", label="Transcripts")]
    assert "documents_due:columbia-msfe" not in {r.id for r in _run(snap)}
    keys = {p.key for p in standard_items.propose(columbia, cfg.standard_items) if p.kind == "document"}
    updated, _, added = standard_items.apply(columbia, keys, cfg.standard_items)
    assert added and all(d.status.value == "not_requested" for d in added)
    columbia.documents = updated.documents
    assert columbia.documents and "documents_due:columbia-msfe" in {r.id for r in _run(snap)}


def test_when_every_deadline_passed_only_scheduled_interviews_remain(snap: Snapshot) -> None:
    later = [r for r in _run(snap, date(2027, 1, 16))]
    assert later == []  # the math test (19 Dec) is past too


# ---- unverified data stays visible (rule 11; DECISIONS #106 Q4) -----------------------------------------


def test_unverified_deadlines_are_marked_in_titles_and_descriptions(snap: Snapshot) -> None:
    by_id = {r.id: r for r in _run(snap)}
    assert by_id["deadline:princeton-mfin"].title == "[Hub] Princeton MFin: application deadline (unverified)"
    assert (
        by_id["deadline_alert_7:princeton-mfin"].title
        == "[Hub] Princeton MFin: deadline in 7 days (unverified)"
    )
    assert "Data: Unverified · Last cycle's date" in by_id["documents_due:princeton-mfin"].description
    princeton = _prog(snap, "princeton-mfin")
    princeton.verification = Verification.VERIFIED
    princeton.deadlines[0].verification = Verification.VERIFIED
    by_id = {r.id: r for r in _run(snap)}
    assert by_id["deadline:princeton-mfin"].title == "[Hub] Princeton MFin: application deadline"
    assert "Data: Verified" in by_id["deadline:princeton-mfin"].description


# ---- overdue tasks (DECISIONS #106 Q2) --------------------------------------------------------------


def test_overdue_lists_past_tasks_whose_condition_still_holds(snap: Snapshot) -> None:
    got = engine.overdue(
        snap.programmes, snap.professors, snap.settings.reminder_tz, load_config(), date(2026, 12, 2)
    )
    assert [r.id for r in got] == [
        "letters_request_due:princeton-mfin",  # 3 Nov
        "letter_follow_up:princeton-mfin:example-a",  # 5 Nov
        "documents_due:princeton-mfin",  # 24 Nov
        "letters_request_due:booth-mif",  # 26 Nov
        "letters_check:princeton-mfin",  # 1 Dec; deadline_alert_14 the same day is a notice, never overdue
    ]
    assert all(r.overdue for r in got)


def test_nothing_is_overdue_once_the_deadline_passed_or_the_task_is_done(snap: Snapshot) -> None:
    cfg = load_config()
    assert not [
        r
        for r in engine.overdue(
            snap.programmes, snap.professors, snap.settings.reminder_tz, cfg, date(2026, 12, 16)
        )
        if r.programme_id == "princeton-mfin"
    ]
    _prog(snap, "princeton-mfin").status = ProgrammeStatus.SUBMITTED
    assert not [
        r
        for r in engine.overdue(
            snap.programmes, snap.professors, snap.settings.reminder_tz, cfg, date(2026, 12, 2)
        )
        if r.programme_id == "princeton-mfin"
    ]
