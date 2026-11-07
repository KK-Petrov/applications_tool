"""Payloads for the AI skills, built in Python from stored data (docs/04 §2.1; rule 13).

Facts the model must copy (deadlines, the subject) are formatted here; the model never computes them.
Only the programmes ticked for this request are included, so an unselected programme can't leak in.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from hub.config import Config
from hub.models.biography import Biography
from hub.models.common import Verification
from hub.models.course import TranscriptCourse
from hub.models.professor import Professor
from hub.models.programme import Programme
from hub.rules import names
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
    courses: Sequence[TranscriptCourse] = (),
) -> dict[str, Any]:
    """The ``/rec-email`` input: owner, professor, ticked programmes (by deadline), biography, constraints.

    ``courses`` holds every course linked through ``course_ids`` (pass the stored courses), or the
    professor's manual course and description when none is linked (docs/04 §2.1, DECISIONS #44).
    ``surname`` is the salutation form: the stored surname, else the name's surname with its particle
    (docs/02 §9: "Van den Berg" standing alone). ``submission`` is null: no stored field says how letters
    are submitted (DECISIONS #48), and any programme-specific note is in ``letters_note``.
    """
    email = config.email
    return {
        "owner": {"name": owner_name},
        "professor": {
            "name": professor.name,
            "title": professor.title,
            "surname": salutation_surname(
                professor.surname or names.parse(professor.name, config.names).surname
            ),
            "university": professor.university,
            "courses": _courses(professor, courses),
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


MANUAL_DESCRIPTION_WARNING = "Check which course this description belongs to."


def _course_entry(c: TranscriptCourse, description: str | None = None) -> dict[str, Any]:
    return {
        "name": c.name,
        "code": c.code,
        "description": c.description if description is None else description,
        "grade": c.grade,
        "term": c.term,
        "credits": c.credits,
    }


def _manual_entry(professor: Professor) -> dict[str, Any]:
    return {
        "name": professor.course,
        "code": None,
        "description": professor.course_description,
        "grade": professor.grade,
        "term": professor.term,
        "credits": None,
    }


def _manual_target(professor: Professor, linked: list[TranscriptCourse]) -> TranscriptCourse | None | str:
    """Which linked course the professor's manual description belongs to (DECISIONS #66):
    (a) the one whose name, or "name (code)" as Add professor's "Use" writes it, equals the manual course,
    ignoring case; (b) else the only linked course without a description; (c) "separate" otherwise.
    None when there is no manual description."""
    if not professor.course_description.strip():
        return None
    manual = " ".join(professor.course.split()).casefold()
    for c in linked:
        labels = {c.name.casefold(), f"{c.name} ({c.code})".casefold() if c.code else ""}
        if manual and manual in labels:
            return c
    empty = [c for c in linked if not c.description.strip()]
    return empty[0] if len(empty) == 1 else "separate"


def _courses(professor: Professor, stored: Sequence[TranscriptCourse]) -> list[dict[str, Any]]:
    """Every linked course; the manual course and description when none is linked. With links, a manual
    description fills the course it belongs to (or comes as its own entry, with a warning). The stored courses
    are never changed: only the payload uses the text."""
    by_id = {c.id: c for c in stored}
    linked = [by_id[i] for i in professor.course_ids if i in by_id]
    if not linked:
        return [_manual_entry(professor)]
    target = _manual_target(professor, linked)
    entries = [_course_entry(c, professor.course_description if c is target else None) for c in linked]
    if target == "separate":
        entries.append(_manual_entry(professor))
    return entries


def course_warnings(professor: Professor, stored: Sequence[TranscriptCourse]) -> list[str]:
    """The draft's warning when the manual description is sent as a course of its own (rule (c))."""
    by_id = {c.id: c for c in stored}
    linked = [by_id[i] for i in professor.course_ids if i in by_id]
    return [MANUAL_DESCRIPTION_WARNING] if linked and _manual_target(professor, linked) == "separate" else []


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
