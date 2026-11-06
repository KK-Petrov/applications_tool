"""The email eval set (docs/04 §4): 10 invented cases and 3 calibration emails, loaded as the app would.

Each case is a professor record as it would be stored (``professors/<id>.yaml``), the programmes ticked for
the request, and optional extra biography entries. Payloads are built by the app's own payload builder
(``hub.ai.payloads``) with the clock fixed at Friday 30 October 2026, 12:00 New York, against frozen copies
of the fixture programmes in ``tests/fixtures/email_eval/programmes/`` (invented owner biography
alongside). Cases 8–10 are held out from prompt tuning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from hub.ai.email_checks import anticipated_terms, unselected_terms
from hub.ai.payloads import rec_email_payload
from hub.config import Config, load_config
from hub.models.biography import Biography, BiographyEntry
from hub.models.professor import Professor
from hub.models.programme import Programme

HERE = Path(__file__).resolve().parent
CASES_DIR = HERE / "cases"
CALIBRATION_DIR = HERE / "calibration"
# Invented data files live under tests/fixtures/ (the privacy guard, C-0-03, allows them only there).
DATA_DIR = HERE.parents[1] / "fixtures" / "email_eval"
PROGRAMMES_DIR = DATA_DIR / "programmes"
NOW = datetime(2026, 10, 30, 16, 0, tzinfo=UTC)  # Friday 30 October 2026, 12:00 New York


def _yaml(path: Path) -> Any:
    return YAML(typ="safe").load(path.read_text(encoding="utf-8"))


@dataclass
class Case:
    number: int
    path: Path
    held_out: bool
    programme_ids: list[str]
    professor: Professor
    biography: Biography
    anticipated: list[str] = field(default_factory=list)  # key phrases that must stay "expected"


@dataclass
class Built:
    """A case ready to run: the payload, and what the graders need beyond it."""

    payload: dict[str, Any]
    unselected: list[str]  # names of programmes not in this request
    anticipated: list[str]  # the case's own terms plus those derived from anticipated entries


@dataclass
class CalibrationItem:
    number: int
    path: Path
    case: Case
    planted: str  # the one invented claim the grader must flag
    body: str


@cache
def programmes() -> tuple[Programme, ...]:
    return tuple(Programme.model_validate(_yaml(p)) for p in sorted(PROGRAMMES_DIR.glob("*.yaml")))


@cache
def base_biography() -> Biography:
    return Biography.model_validate(_yaml(DATA_DIR / "biography.yaml"))


def owner_name() -> str:
    return str(_yaml(HERE / "owner.yaml")["name"])


def _case(path: Path) -> Case:
    raw = _yaml(path)
    extra = [BiographyEntry.model_validate(e) for e in raw.get("biography_extra") or []]
    return Case(
        number=int(raw["number"]),
        path=path,
        held_out=bool(raw["held_out"]),
        programme_ids=list(raw["programmes"]),
        professor=Professor.model_validate(raw["professor"]),
        biography=Biography(entries=[*base_biography().entries, *extra]),
        anticipated=list(raw.get("anticipated") or []),
    )


def load_cases() -> list[Case]:
    return sorted((_case(p) for p in CASES_DIR.glob("*.yaml")), key=lambda c: c.number)


def build_payload(case: Case, config: Config | None = None) -> Built:
    config = config or load_config()
    chosen = [p for p in programmes() if p.id in case.programme_ids]
    rest = [p for p in programmes() if p.id not in case.programme_ids]
    payload = rec_email_payload(case.professor, chosen, case.biography, owner_name(), NOW, config)
    terms: dict[str, str] = {}  # one per phrase, ignoring case
    for term in [*case.anticipated, *anticipated_terms(case.biography.entries)]:
        terms.setdefault(term.casefold(), term)
    return Built(payload, unselected_terms(chosen, rest), list(terms.values()))


def load_calibration() -> list[CalibrationItem]:
    cases = {c.number: c for c in load_cases()}
    items = []
    for path in sorted(CALIBRATION_DIR.glob("*.yaml")):
        raw = _yaml(path)
        items.append(
            CalibrationItem(
                number=int(raw["number"]),
                path=path,
                case=cases[int(raw["case"])],
                planted=str(raw["planted"]),
                body=str(raw["body"]),
            )
        )
    return items
