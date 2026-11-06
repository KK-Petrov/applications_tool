"""Payloads for the AI skills, built in Python from stored data (docs/04 §2.1; rule 13).

Facts the model must copy (deadlines, the subject) are formatted here; the model never computes them.
Only the programmes ticked for this request are included, so an unselected programme can't leak in.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from hub.config import Config
from hub.models.biography import Biography
from hub.models.common import Verification
from hub.models.professor import Professor
from hub.models.programme import Programme
from hub.rules.names import salutation_surname
from hub.rules.programmes import deadline_utc, primary_deadline
from hub.rules.timezones import format_deadline_text, to_instant


def _by_deadline(programmes: list[Programme], now_utc: datetime) -> list[Programme]:
    return sorted(programmes, key=lambda p: (deadline_utc(primary_deadline(p, now_utc)), p.id))


def rec_email_payload(
    professor: Professor,
    programmes: list[Programme],
    biography: Biography,
    owner_name: str,
    now_utc: datetime,
    config: Config,
) -> dict[str, Any]:
    """The ``/rec-email`` input: owner, professor, ticked programmes (by deadline), biography, constraints.

    ``courses`` holds the professor's manual course and description; linked courses (``course_ids``) arrive
    in Phase 2c. ``submission`` is null: no stored field says how letters are submitted (DECISIONS #48), and
    any programme-specific note is in ``letters_note``.
    """
    email = config.email
    return {
        "owner": {"name": owner_name},
        "professor": {
            "name": professor.name,
            "title": professor.title,
            "surname": salutation_surname(professor.resolved_surname),
            "university": professor.university,
            "courses": [
                {
                    "name": professor.course,
                    "code": None,
                    "description": professor.course_description,
                    "grade": professor.grade,
                    "term": professor.term,
                    "credits": None,
                }
            ],
            "interaction": professor.interaction,
            "tone": professor.tone.value,
        },
        "programmes": [
            {
                "short_name": p.short_name,
                "full_name": p.name,
                "deadline_text": _deadline_text(p, now_utc, config),
                "submission": None,
                "letters_note": p.letters_note,
            }
            for p in _by_deadline(programmes, now_utc)
        ],
        "biography": [
            {
                "id": e.id,
                "category": e.category.value,
                "title": e.title,
                "detail": e.detail,
                "state": e.state.value,
                "dates": e.dates,
            }
            for e in biography.entries
        ],
        "constraints": {
            "min_words": email.min_words,
            "max_words": email.max_words,
            "subject": email.subject,
            "sign_off": email.sign_off,
        },
    }


def _deadline_text(programme: Programme, now_utc: datetime, config: Config) -> str:
    d = primary_deadline(programme, now_utc)
    return format_deadline_text(
        d.due_local,
        d.tz,
        d.time_known,
        config.time,
        config.email.deadline_format,
        config.email.deadline_date_only_format,
    )


UNVERIFIED_SUFFIX = "'s deadline is unverified — check it before sending"


def is_unverified_warning(warning: str) -> bool:
    """True for the warnings that come first and stand out in the result (DECISIONS #48)."""
    return warning.endswith(UNVERIFIED_SUFFIX)


def programmes_by_deadline(programmes: list[Programme], now_utc: datetime) -> list[Programme]:
    """Programmes in the order the email lists them: by primary deadline."""
    return _by_deadline(programmes, now_utc)


def deadline_warnings(programmes: list[Programme], now_utc: datetime) -> list[str]:
    """Warnings about the deadlines an email states, unverified ones first (rule 11; DECISIONS #48)."""
    ordered = _by_deadline(programmes, now_utc)
    unverified = [
        f"{p.short_name}{UNVERIFIED_SUFFIX}"
        for p in ordered
        if primary_deadline(p, now_utc).verification is not Verification.VERIFIED
        or p.verification is not Verification.VERIFIED
    ]
    unknown_time = [
        f"{p.short_name}'s deadline time is not confirmed; the email gives the date only"
        for p in ordered
        if not primary_deadline(p, now_utc).time_known
    ]
    moved = [  # docs/02 §1: a local time inside a daylight-saving gap is moved forward and flagged
        f"{p.short_name}'s deadline time falls in a daylight-saving change and is given as the moved time"
        for p in ordered
        if primary_deadline(p, now_utc).time_known
        and to_instant(primary_deadline(p, now_utc).due_local, primary_deadline(p, now_utc).tz).adjusted
    ]
    return unverified + unknown_time + moved
