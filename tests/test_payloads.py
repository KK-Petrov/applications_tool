"""The /rec-email payload (docs/04 §2.1), built in Python from the stored data.

Deadlines are formatted in the **school's** zone and copied exactly by the model; only ticked programmes
are included; the biography may be empty until Phase 4a; the subject is built in Python (DECISIONS, 2b).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hub.ai.payloads import deadline_warnings, rec_email_payload
from hub.models.biography import Biography
from hub.models.common import Verification
from hub.models.professor import Professor
from hub.rules.names import salutation_surname
from hub.store import Store

from helpers import FIXED_NOW

SUBJECT = "Request for a letter of recommendation: master's programmes in finance"


def _payload(config, pid: str, programme_ids: list[str], *, biography: Biography | None = None) -> dict:
    snap = Store.from_config(config).load_all()
    professor = next(p for p in snap.professors if p.id == pid)
    programmes = [p for p in snap.programmes if p.id in programme_ids]
    bio = snap.biography if biography is None else biography
    return rec_email_payload(professor, programmes, bio, snap.settings.owner_name, FIXED_NOW, config)


def test_payload_shape_and_professor(config) -> None:
    payload = _payload(config, "jansen", ["princeton-mfin"])
    assert set(payload) == {"owner", "professor", "programmes", "biography", "constraints"}
    assert payload["owner"] == {"name": "Alex Example"}
    assert payload["professor"] == {
        "name": "Prof. Dr. Eva Jansen",
        "title": "Professor",
        "surname": "Jansen",
        "university": "RSM",
        "courses": [
            {
                "name": "Corporate Finance (FIN-101)",
                "code": None,
                "description": (
                    "An invented course description used only in tests.\nIt spans two lines on purpose.\n"
                ),
                "grade": "8.5 / 10",
                "term": "Fall 2025",
                "credits": None,
            }
        ],
        "interaction": "Asked questions after every lecture",
        "tone": "formal",
    }
    json.dumps(payload)  # plain JSON, nothing else


def test_deadline_text_is_in_the_school_zone_and_only_ticked_programmes_appear(config) -> None:
    payload = _payload(config, "jansen", ["booth-mif", "princeton-mfin"])
    assert [p["short_name"] for p in payload["programmes"]] == ["Princeton MFin", "Booth MiF"]  # by deadline
    princeton, booth = payload["programmes"]
    assert princeton == {
        "short_name": "Princeton MFin",
        "full_name": "Princeton Master in Finance",
        "deadline_text": "15 December 2026, 11:59 pm ET",
        "submission": None,
        "letters_note": "Without full-time work experience, two letters must be academic",
    }
    assert booth["deadline_text"] == "7 January 2027"  # time not confirmed: the date only
    text = json.dumps(payload)
    for unticked in ("MIT Sloan", "Columbia", "MSFE"):
        assert unticked not in text


def test_deadline_text_does_not_follow_the_display_zone(config, data_dir: Path) -> None:
    settings = data_dir / "settings.yaml"
    settings.write_text(
        settings.read_text(encoding="utf-8").replace(
            "display_tz: America/New_York", "display_tz: Europe/Amsterdam"
        ),
        encoding="utf-8",
    )
    payload = _payload(config, "jansen", ["princeton-mfin"])
    assert payload["programmes"][0]["deadline_text"] == "15 December 2026, 11:59 pm ET"


def test_constraints_and_subject_come_from_config(config) -> None:
    payload = _payload(config, "jansen", ["princeton-mfin"])
    assert payload["constraints"] == {
        "min_words": 180,
        "max_words": 280,
        "subject": SUBJECT,
        "sign_off": "Kind regards,",
    }


def test_biography_entries_are_passed_and_may_be_empty(config, data_dir: Path) -> None:
    payload = _payload(config, "jansen", ["princeton-mfin"])
    assert payload["biography"] == [
        {
            "id": "test-score",
            "category": "test",
            "title": "Invented test score 700",
            "detail": "Invented detail",
            "state": "actual",
            "dates": None,
        },
        {
            "id": "cum-laude",
            "category": "honour",
            "title": "Cum laude (expected)",
            "detail": None,
            "state": "anticipated",
            "dates": "Jun 2027",
        },
    ]
    (data_dir / "biography.yaml").unlink()
    assert _payload(config, "jansen", ["princeton-mfin"])["biography"] == []


def test_defaults_for_a_sparse_professor(config) -> None:
    prof = _payload(config, "whitfield", ["princeton-mfin"])["professor"]
    assert prof["surname"] == "Whitfield"  # last token of the name
    assert prof["title"] == "Professor"
    assert prof["tone"] == "warm"
    assert prof["interaction"] is None
    assert prof["courses"][0]["grade"] is None and prof["courses"][0]["term"] is None


def test_title_and_particle_surname_give_the_salutation_form(config) -> None:
    snap = Store.from_config(config).load_all()
    base = next(p for p in snap.professors if p.id == "jansen")
    prof = Professor.model_validate(
        {
            **base.model_dump(),
            "id": "van-der-berg",
            "name": "Prof. van der Berg",
            "surname": "van der Berg",
            "title": "Dr.",
        }
    )
    princeton = [p for p in snap.programmes if p.id == "princeton-mfin"]
    payload = rec_email_payload(prof, princeton, snap.biography, "Alex Example", FIXED_NOW, config)
    assert payload["professor"]["title"] == "Dr."
    assert payload["professor"]["surname"] == "Van der Berg"


@pytest.mark.parametrize(
    ("stored", "salutation"),
    [
        ("van der Berg", "Van der Berg"),
        ("de Vries", "De Vries"),
        ("Jansen", "Jansen"),
        ("van den Berg", "Van den Berg"),
    ],
)
def test_salutation_surname(stored: str, salutation: str) -> None:
    assert salutation_surname(stored) == salutation


def test_unverified_deadline_warnings_come_first(config) -> None:
    snap = Store.from_config(config).load_all()
    chosen = [p for p in snap.programmes if p.id in ("princeton-mfin", "booth-mif")]
    warnings = deadline_warnings(chosen, FIXED_NOW)
    assert warnings[:2] == [
        "Princeton MFin's deadline is unverified — check it before sending",
        "Booth MiF's deadline is unverified — check it before sending",
    ]
    assert "Booth MiF's deadline time is not confirmed; the email gives the date only" in warnings[2:]
    princeton = next(p for p in chosen if p.id == "princeton-mfin")
    princeton.verification = Verification.VERIFIED
    princeton.verification_note = None
    for d in princeton.deadlines:
        d.verification = Verification.VERIFIED
    assert not any(w.startswith("Princeton") for w in deadline_warnings([princeton], FIXED_NOW))


# ---- hub email-payload (DECISIONS #48 (6)): what "Refine in Claude Code" runs ----------------------


def test_email_payload_command_prints_the_payload(config, data_dir: Path) -> None:
    from typer.testing import CliRunner

    from hub.cli import app

    from helpers import tree_digest

    before = tree_digest(data_dir)
    out = CliRunner().invoke(app, ["email-payload", "jansen"])
    assert out.exit_code == 0, out.output
    payload = json.loads(out.output)
    assert [p["short_name"] for p in payload["programmes"]] == ["Princeton MFin"]  # To ask / Draft ready only
    chosen = CliRunner().invoke(app, ["email-payload", "jansen", "--programme", "booth-mif"])
    assert [p["short_name"] for p in json.loads(chosen.output)["programmes"]] == ["Booth MiF"]
    missing = CliRunner().invoke(app, ["email-payload", "nobody"])
    assert missing.exit_code == 1
    assert tree_digest(data_dir) == before


def test_a_deadline_in_a_dst_gap_is_moved_and_warned_about(config) -> None:
    """docs/02 §1: 02:30 on 14 March 2027 New York doesn't exist; it becomes 03:30 and is flagged."""
    snap = Store.from_config(config).load_all()
    princeton = next(p for p in snap.programmes if p.id == "princeton-mfin")
    princeton.deadlines[0].due_local = princeton.deadlines[0].due_local.replace(
        year=2027, month=3, day=14, hour=2, minute=30
    )
    jansen = next(p for p in snap.professors if p.id == "jansen")
    payload = rec_email_payload(jansen, [princeton], snap.biography, "Alex Example", FIXED_NOW, config)
    assert payload["programmes"][0]["deadline_text"] == "14 March 2027, 3:30 am ET"
    warnings = deadline_warnings([princeton], FIXED_NOW)
    assert any("daylight-saving" in w and "Princeton MFin" in w for w in warnings), warnings


def test_linked_courses_are_all_in_the_payload_and_the_manual_course_otherwise(config) -> None:
    """Phase 2c: ``courses`` holds every linked course (``course_ids``); without links, the manual course."""
    from datetime import UTC, datetime

    from hub.ai.payloads import rec_email_payload
    from hub.models.course import TranscriptCourse
    from hub.store import Store

    snapshot = Store.from_config(config).load_all()
    prof = next(p for p in snapshot.professors if p.id == "jansen")
    owner = snapshot.settings.owner_name
    linked = [
        TranscriptCourse(
            id="c-one",
            name="Investments",
            code="FEB12006",
            grade="9,0",
            credits=6,
            description="Portfolio choice.",
        ),
        TranscriptCourse(id="c-two", name="Derivatives and Risk", code="FEB12007", grade="8,0"),
    ]
    now = datetime(2026, 10, 30, 16, tzinfo=UTC)
    with_links = prof.model_copy(update={"course_ids": ["c-one", "c-two", "deleted-course"]})
    payload = rec_email_payload(
        with_links, snapshot.programmes[:1], snapshot.biography, owner, now, config, linked
    )
    courses = payload["professor"]["courses"]
    assert [(c["name"], c["code"], c["grade"], c["credits"]) for c in courses] == [
        ("Investments", "FEB12006", "9,0", 6.0),
        ("Derivatives and Risk", "FEB12007", "8,0", None),
    ]
    assert courses[0]["description"] == "Portfolio choice."
    manual = rec_email_payload(prof, snapshot.programmes[:1], snapshot.biography, owner, now, config, linked)
    assert [c["name"] for c in manual["professor"]["courses"]] == [prof.course]


# ---- the manual description with linked courses (user decision, DECISIONS #66) --------------------------


def _manual_case(config, course: str, linked: list) -> tuple[list[dict], list[str]]:  # noqa: ANN001
    from datetime import UTC, datetime

    from hub.ai.payloads import course_warnings, rec_email_payload
    from hub.store import Store

    snapshot = Store.from_config(config).load_all()
    prof = next(p for p in snapshot.professors if p.id == "jansen")
    prof = prof.model_copy(
        update={
            "course_ids": [c.id for c in linked],
            "course": course,
            "course_description": "Pasted syllabus.",
        }
    )
    now = datetime(2026, 10, 30, 16, tzinfo=UTC)
    payload = rec_email_payload(
        prof, snapshot.programmes[:1], snapshot.biography, snapshot.settings.owner_name, now, config, linked
    )
    return payload["professor"]["courses"], course_warnings(prof, linked)


def _course(cid: str, name: str, description: str = "", code: str | None = None):  # noqa: ANN202
    from hub.models.course import TranscriptCourse

    return TranscriptCourse(id=cid, name=name, code=code, description=description)


def test_a_the_linked_course_with_the_manual_name_gets_the_description(config) -> None:
    linked = [_course("ca", "Investments", "From the transcript."), _course("cb", "Derivatives", code="FEB7")]
    for manual in ("investments", "Derivatives (FEB7)"):  # the name, or "name (code)" as Use fills it
        courses, warnings = _manual_case(config, manual, linked)
        target = "Investments" if manual == "investments" else "Derivatives"
        assert next(c for c in courses if c["name"] == target)["description"] == "Pasted syllabus."
        assert len(courses) == 2 and warnings == []
    assert linked[0].description == "From the transcript."  # the stored course is never changed


def test_b_else_the_only_linked_course_without_a_description_gets_it(config) -> None:
    linked = [_course("ca", "Investments", "From the transcript."), _course("cb", "Derivatives")]
    courses, warnings = _manual_case(config, "Something else", linked)
    assert [c["description"] for c in courses] == ["From the transcript.", "Pasted syllabus."]
    assert warnings == []


def test_c_otherwise_the_manual_course_is_its_own_entry_with_a_warning(config) -> None:
    linked = [_course("ca", "Investments"), _course("cb", "Derivatives")]  # two without a description
    courses, warnings = _manual_case(config, "Something else", linked)
    assert [c["name"] for c in courses] == ["Investments", "Derivatives", "Something else"]
    assert courses[-1]["description"] == "Pasted syllabus."
    assert warnings == ["Check which course this description belongs to."]
