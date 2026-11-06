"""Shared field types for the data files (docs/01 §1).

- ``Slug``: IDs are lowercase slugs ``[a-z0-9-]{2,60}``.
- ``LocalDateTime``: a wall-clock time ``YYYY-MM-DDTHH:MM`` with **no** offset; it is always paired with
  an IANA zone field next to it, and is written back in exactly that form.
- ``IanaZone``: a zone name ``zoneinfo`` knows (e.g. ``America/New_York``).
- ``AwareTimestamp``: when something happened (saved, generated, synced, evaluated), stored as ISO 8601
  with its UTC offset, e.g. ``2026-11-04T14:05:00-05:00`` (DECISIONS #18).
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    PlainSerializer,
    StringConstraints,
)

LOCAL_DATETIME_FORMAT = "%Y-%m-%dT%H:%M"
_LOCAL_DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")


class DataModel(BaseModel):
    """Base for every data-file model: unknown keys are errors, text is never stripped or altered."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False, validate_assignment=True)


def _parse_local_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is not None or value.second or value.microsecond:
            raise ValueError("must be a local time YYYY-MM-DDTHH:MM without seconds or offset")
        return value
    if not isinstance(value, str) or not _LOCAL_DATETIME_RE.fullmatch(value):
        raise ValueError(f"must be a local time YYYY-MM-DDTHH:MM without seconds or offset, got {value!r}")
    return datetime.strptime(value, LOCAL_DATETIME_FORMAT)


def _check_zone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"{value!r} is not an IANA time zone (e.g. America/New_York)") from exc
    return value


def _check_email(value: str) -> str:
    if not _EMAIL_RE.fullmatch(value):
        raise ValueError(f"{value!r} is not an email address")
    return value


Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]{2,60}$")]
LocalDateTime = Annotated[
    datetime,
    BeforeValidator(_parse_local_datetime),
    PlainSerializer(lambda v: v.strftime(LOCAL_DATETIME_FORMAT), return_type=str),
]
IanaZone = Annotated[str, AfterValidator(_check_zone)]


def _plain_datetime(value: datetime) -> datetime:
    """Rebuild as a plain ``datetime``: ruamel's ``TimeStamp`` subclass loses its zone on deepcopy and is
    written in its own format, so it must never live inside a model."""
    return datetime(
        value.year, value.month, value.day, value.hour, value.minute, value.second, value.microsecond,
        tzinfo=value.tzinfo, fold=value.fold,
    )  # fmt: skip


# A stored instant: ISO 8601 with its UTC offset; naive values are rejected.
AwareTimestamp = Annotated[AwareDatetime, AfterValidator(_plain_datetime)]
Email = Annotated[str, AfterValidator(_check_email)]
Url = Annotated[str, StringConstraints(pattern=r"^https?://\S+$")]
# A lowercase host name such as ``columbia.edu`` (official source domains, DECISIONS #37)
Domain = Annotated[
    str,
    StringConstraints(max_length=253, pattern=r"^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}$"),
]


class Verification(StrEnum):
    """Whether the user has confirmed data against its source (rule 11: only the user sets ``verified``)."""

    UNVERIFIED = "unverified"
    VERIFIED = "verified"


def unique_ids(items: list[Any], attr: str = "id") -> list[Any]:
    """Validator helper: IDs must be unique within their list."""
    seen: set[Any] = set()
    for item in items:
        key = getattr(item, attr)
        if key in seen:
            raise ValueError(f"duplicate {attr} {key!r}")
        seen.add(key)
    return items
