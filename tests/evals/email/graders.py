"""Graders for the email eval (docs/04 §4; C-2b-01 to C-2b-04). Never edited to make a run pass.

- ``schema_failures``: the output fails the rec-email schema, or the call failed (never dropped).
- ``fact_failures``: emails with a fact problem (``hub.ai.email_checks.fact_problems``).
- ``form_failures``: emails with a form problem (``form_problems``: 180–280 words, exact salutation,
  anticipated entries kept as expected).
- ``unsupported_claims``: total claims flagged by the ``/grade-claims`` skill over the emails.
- ``grader_calibration_passed``: the claim grader flagged the planted claim in all 3 calibration emails.
- ``subject_mismatches`` (reported, not gated): the subject differs from the one built in Python.
Tuning cases (1–7) and held-out cases (8–10) are also counted separately.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from hub.ai.email_checks import fact_problems, form_problems, subject_matches
from hub.rules.words import count_words

OVERLAP_WORDS = 4  # a flagged quote counts for the planted claim when they share this many words in a row


@dataclass
class CaseGrade:
    case_id: str
    held_out: bool
    schema_ok: bool
    schema_error: str | None = None
    fact_problems: list[str] = field(default_factory=list)
    form_problems: list[str] = field(default_factory=list)
    subject_ok: bool | None = None
    words: int | None = None
    unsupported: list[dict[str, str]] = field(default_factory=list)
    claims_error: str | None = None  # the claim grader itself failed for this email


def grade_output(
    case_id: str,
    held_out: bool,
    output: dict[str, Any] | None,
    error: str | None,
    payload: dict[str, Any],
    unselected: list[str],
    anticipated: list[str],
    schema: dict[str, Any],
) -> CaseGrade:
    """Schema, fact and form grades for one email (``output`` None means the call failed with ``error``)."""
    if output is None:
        return CaseGrade(case_id, held_out, schema_ok=False, schema_error=error or "no output")
    try:
        jsonschema.validate(output, schema)
    except jsonschema.ValidationError as exc:
        return CaseGrade(case_id, held_out, schema_ok=False, schema_error=exc.message)
    body = output["body"]
    return CaseGrade(
        case_id,
        held_out,
        schema_ok=True,
        fact_problems=fact_problems(body, payload, unselected),
        form_problems=form_problems(body, payload, anticipated),
        subject_ok=subject_matches(output["subject"], payload),
        words=count_words(body),
    )


def _words(text: str) -> list[str]:
    return re.findall(r"[\w’']+", text.casefold())


def calibration_flagged(planted: str, claims: list[dict[str, str]]) -> bool:
    """True when some flagged quote is the planted claim: they share ``OVERLAP_WORDS`` words in a row
    (or the whole quote when it is shorter). A flag elsewhere in the email does not count."""
    target = _words(planted)
    grams = {tuple(target[i : i + OVERLAP_WORDS]) for i in range(max(1, len(target) - OVERLAP_WORDS + 1))}
    for claim in claims:
        quote = _words(claim.get("quote", ""))
        if not quote:
            continue
        if len(quote) < OVERLAP_WORDS:
            if " ".join(quote) in " ".join(target):
                return True
            continue
        if any(tuple(quote[i : i + OVERLAP_WORDS]) in grams for i in range(len(quote) - OVERLAP_WORDS + 1)):
            return True
    return False


def _counts(grades: list[CaseGrade]) -> dict[str, int]:
    return {
        "cases": len(grades),
        "schema_failures": sum(not g.schema_ok for g in grades),
        "fact_failures": sum(bool(g.fact_problems) for g in grades),
        "form_failures": sum(bool(g.form_problems) for g in grades),
        "unsupported_claims": sum(len(g.unsupported) for g in grades),
        "subject_mismatches": sum(g.subject_ok is False for g in grades),
    }


def summarise(grades: list[CaseGrade], calibration: list[bool]) -> dict[str, Any]:
    """The metrics of reports/eval_email.json, overall and split into tuning and held-out cases."""
    total = _counts(grades)
    return {
        "schema_failures": total["schema_failures"],
        "fact_failures": total["fact_failures"],
        "form_failures": total["form_failures"],
        "unsupported_claims": total["unsupported_claims"],
        "grader_calibration_passed": bool(calibration) and all(calibration),
        "subject_mismatches": total["subject_mismatches"],
        "grader_errors": sum(g.claims_error is not None for g in grades),
        "tuning": _counts([g for g in grades if not g.held_out]),
        "held_out": _counts([g for g in grades if g.held_out]),
    }


def gates_pass(summary: dict[str, Any]) -> bool:
    """C-2b-01 to C-2b-04 all met (a failed claim-grader call means C-2b-04 can't be shown)."""
    return (
        summary["schema_failures"] == 0
        and summary["fact_failures"] == 0
        and summary["form_failures"] == 0
        and summary["unsupported_claims"] == 0
        and summary["grader_calibration_passed"]
        and summary["grader_errors"] == 0
    )
