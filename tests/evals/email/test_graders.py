"""Offline tests of the email eval graders (docs/04 §4): they must count what C-2b-01 to C-2b-04 count.

The live run (``tasks.py eval-email``) uses these same graders on real outputs; here they get hand-made ones.
"""

from __future__ import annotations

import json

import pytest
from graders import calibration_flagged, grade_output, summarise

from hub.ai.skills import load_schema

SCHEMA = load_schema("rec_email")
PAYLOAD = {
    "owner": {"name": "Alex Example"},
    "professor": {"name": "Prof. A. Example", "title": "Dr.", "surname": "Example", "university": "UVA",
                  "courses": [{"name": "Options (COMM-1)", "code": None, "description": "x", "grade": None,
                               "term": None, "credits": None}],
                  "interaction": None, "tone": "formal"},
    "programmes": [{"short_name": "Princeton MFin", "full_name": "Princeton Master in Finance",
                    "deadline_text": "15 December 2026, 11:59 pm ET", "submission": None,
                    "letters_note": None}],
    "biography": [],
    "constraints": {"min_words": 180, "max_words": 280,
                    "subject": "Request for a letter of recommendation: master's programmes in finance",
                    "sign_off": "Kind regards,"},
}  # fmt: skip


def _good_body(extra: str = "") -> str:
    filler = " ".join(["word"] * 180)
    return (
        "Dear Dr. Example,\n\nI took Options (COMM-1) with you.\n\n"
        f"- Princeton MFin: 15 December 2026, 11:59 pm ET\n\n{filler} {extra}\n\nKind regards,\nAlex Example"
    )


def _output(body: str | None = None, **extra: object) -> dict:
    return {
        "subject": PAYLOAD["constraints"]["subject"],
        "body": body if body is not None else _good_body(),
        "used_biography_ids": [],
        "warnings": [],
        **extra,
    }


def _grade(output: dict | None, error: str | None = None, held_out: bool = False):
    return grade_output("01", held_out, output, error, PAYLOAD, ["Columbia", "MIT"], ["cum laude"], SCHEMA)


def test_a_good_output_passes_every_grader() -> None:
    g = _grade(_output())
    assert g.schema_ok and g.fact_problems == [] and g.form_problems == [] and g.subject_ok


@pytest.mark.parametrize(
    "output",
    [_output(extra_key=1), {k: v for k, v in _output().items() if k != "warnings"}, _output(subject=5)],
    ids=["extra key", "missing key", "wrong type"],
)
def test_schema_breaks_are_schema_failures(output: dict) -> None:
    assert not _grade(output).schema_ok


def test_a_failed_call_counts_as_a_schema_failure_never_dropped() -> None:
    g = _grade(None, error="timeout")
    assert not g.schema_ok and g.schema_error == "timeout"


def test_fact_and_form_problems_are_recorded() -> None:
    assert _grade(_output(_good_body("I also apply to Columbia."))).fact_problems
    assert _grade(_output(_good_body().replace("15 December", "15 Dec"))).fact_problems
    assert _grade(_output(_good_body("I graduated cum laude."))).form_problems
    assert _grade(_output(_good_body().replace("Dear Dr. Example,", "Dear Professor Example,"))).form_problems


def test_subject_mismatch_is_recorded_but_is_not_a_fact_or_form_failure() -> None:
    g = _grade(_output(subject="Letter request"))
    assert g.subject_ok is False and g.fact_problems == [] and g.form_problems == []


@pytest.mark.parametrize(
    ("claims", "flagged"),
    [
        ([{"quote": "I received the highest grade in your class", "reason": "not in payload"}], True),
        ([{"quote": "received the highest grade in your class of 120 students", "reason": "x"}], True),
        ([{"quote": "I greatly enjoyed your course", "reason": "x"}], False),
        ([], False),
    ],
)
def test_calibration_counts_only_a_flag_on_the_planted_claim(claims: list[dict], flagged: bool) -> None:
    planted = "I received the highest grade in your class of 120 students."
    assert calibration_flagged(planted, claims) is flagged


def test_summary_counts_and_splits_tuning_and_held_out() -> None:
    good = _grade(_output())
    good.unsupported = []
    bad_fact = _grade(_output(_good_body("MIT too.")))
    bad_fact.unsupported = [{"quote": "q", "reason": "r"}, {"quote": "q2", "reason": "r"}]
    held_bad_form = _grade(_output(_good_body("I graduated cum laude.")), held_out=True)
    held_bad_form.unsupported = []
    failed = _grade(None, error="invalid_output", held_out=True)
    summary = summarise([good, bad_fact, held_bad_form, failed], [True, True, False])
    assert summary["schema_failures"] == 1
    assert summary["fact_failures"] == 1
    assert summary["form_failures"] == 1
    assert summary["unsupported_claims"] == 2
    assert summary["grader_calibration_passed"] is False
    assert summary["tuning"] == {
        "cases": 2, "schema_failures": 0, "fact_failures": 1, "form_failures": 0, "unsupported_claims": 2,
        "subject_mismatches": 0,
    }  # fmt: skip
    assert summary["held_out"]["cases"] == 2
    assert summary["held_out"]["schema_failures"] == 1 and summary["held_out"]["form_failures"] == 1
    json.dumps(summary)
