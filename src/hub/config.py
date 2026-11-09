"""Typed access to ``config/project.yaml``, the single home of every tunable parameter.

``HUB_DATA_DIR`` (environment variable) overrides ``storage.data_dir``; tests always set it so they
never touch the owner's real data folder. ``HUB_CLAUDE_PATH`` overrides ``claude.path``; tests set it to
the fake Claude Code so they never reach the real CLI.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from ruamel.yaml import YAML

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "project.yaml"
DATA_DIR_ENV = "HUB_DATA_DIR"
CLAUDE_PATH_ENV = "HUB_CLAUDE_PATH"  # overrides claude.path; every non-live test points it at the fake


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StorageConfig(_Section):
    data_dir: Path
    log_max_bytes: int = Field(gt=0)
    log_backup_count: int = Field(ge=0)
    write_retries: int = Field(ge=1)
    write_retry_delay_s: float = Field(ge=0)


class AppConfig(_Section):
    host: str
    port: int = Field(gt=0, lt=65536)


class DisplayZone(_Section):
    """One option of the header zone selector."""

    tz: str
    label: str
    clock: Literal["12h", "24h"]
    place: str | None = None  # printed after the abbreviation, e.g. "05:59 CET (Rotterdam)" (DECISIONS #20)


class TimeFormats(_Section):
    """Format patterns for times (docs/02 §1). Placeholders: {day} {month} {year} {time} {zone};
    {hour12} {hour24} {minute} {ampm}."""

    long: str
    short: str
    date: str
    time_12h: str
    time_24h: str


class TimeConfig(_Section):
    display_zones: list[DisplayZone] = Field(min_length=1)
    zone_abbreviations: dict[str, str] = Field(default_factory=dict)
    default_clock: Literal["12h", "24h"]
    formats: TimeFormats
    default_display_tz: str
    default_reminder_tz: str
    assumed_deadline_time: str

    @model_validator(mode="after")
    def _zones_known(self) -> TimeConfig:
        for zone in [z.tz for z in self.display_zones] + [self.default_reminder_tz]:
            try:
                ZoneInfo(zone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError(f"{zone!r} is not an IANA time zone") from exc
        if self.default_display_tz not in [z.tz for z in self.display_zones]:
            raise ValueError("default_display_tz must be one of display_zones")
        return self


class UrgencyConfig(_Section):
    orange_days: int
    red_days: int


class LettersConfig(_Section):
    default_required: int
    max_extra: int
    follow_up_days: int
    request_soon_days: int  # priority 4 is urgent when request_due is at most this many days away


class RemindersConfig(_Section):
    letters_request_days: int
    documents_days: int
    letters_check_days: int
    essay_final_days: int
    deadline_alert_days: list[int]
    interview_eve_days: int
    time_of_day: str
    rule_event_minutes: int
    deadline_event_minutes: int
    interview_event_minutes: int
    deadline_popups_minutes: list[int]
    interview_popups_minutes: list[int]
    dashboard_upcoming: int


class EmailConfig(_Section):
    """The request email (docs/04 §2): word range, subject and the deadline wording given to the model."""

    min_words: int
    max_words: int
    subject_template: str  # {programme_type}
    programme_type: str
    sign_off: str
    deadline_format: str  # {day} {month_long} {year} {time} {zone}, in the school's zone
    deadline_date_only_format: str  # a deadline whose time is not confirmed: the date only

    @property
    def subject(self) -> str:
        """The subject line, built in Python; the skill copies it exactly."""
        return self.subject_template.format(programme_type=self.programme_type)


class ClaudeConfig(_Section):
    """Headless Claude Code (docs/04 §1.1). ``path`` null → ``claude`` on PATH; env HUB_CLAUDE_PATH wins."""

    backend: Literal["cli"]  # the AI backend (docs/04 §1.2); "api" could be added later
    path: str | None
    timeout_s: int = Field(gt=0)
    research_timeout_s: int = Field(gt=0)
    usage_log: str  # the app's AI usage log, relative to data_dir unless absolute (DECISIONS #60)
    run_usage_log: str  # eval and live calls' usage log, relative to the repository unless absolute
    max_turns: int = Field(ge=1)  # lockdown: turn cap per call
    research_max_turns: int = Field(ge=1)  # turn cap for skills that browse (WebSearch/WebFetch; #61)
    eval_concurrency: int = Field(ge=1)  # parallel live calls in tasks.py eval-email


class EvaluationConfig(_Section):
    ratings: list[str]
    criteria: list[str]
    max_comment_words: int


class ResearchConfig(_Section):
    """Programme research (docs/04 §7): hosts accepted as official besides a programme's own domains."""

    official_shared_domains: list[str]


class StandardItem(_Section):
    """One template row of "Add standard steps and documents" (DECISIONS #97)."""

    key: str = Field(pattern=r"^[a-z0-9-]{1,40}$")
    label: str = Field(min_length=1)
    # whole words in a requirement's id or label (documents: derived from it; steps: linked to it)
    requirement_words: list[str]
    exists_words: list[str] = Field(min_length=1)  # whole words in an existing item's label: already there

    @model_validator(mode="after")
    def _lower_case(self) -> StandardItem:
        for word in [*self.requirement_words, *self.exists_words]:
            if word != word.casefold() or not word.isalnum():
                raise ValueError(f"{self.key}: words are lower-case letters and digits, got {word!r}")
        return self


class StandardItemsConfig(_Section):
    documents: list[StandardItem]
    steps: list[StandardItem]

    @model_validator(mode="after")
    def _keys_unique(self) -> StandardItemsConfig:
        for name, items in (("documents", self.documents), ("steps", self.steps)):
            keys = [i.key for i in items]
            if len(set(keys)) != len(keys):
                raise ValueError(f"standard_items.{name}: keys must be unique")
        return self


class NamesConfig(_Section):
    """Parsing names (docs/02 §9): particles that belong to the surname, title tokens and their ranking."""

    particles: list[str]
    title_tokens: dict[str, str]  # lower-case token without full stop → title, e.g. "dr" → "Dr."
    title_precedence: list[str]  # highest first

    @model_validator(mode="after")
    def _titles_ranked(self) -> NamesConfig:
        missing = set(self.title_tokens.values()) - set(self.title_precedence)
        if missing:
            raise ValueError(f"title_precedence must rank every title: missing {sorted(missing)}")
        return self


class IdentifierLabels(_Section):
    en: list[str]
    nl: list[str]

    @property
    def all(self) -> list[str]:
        return [*self.en, *self.nl]


class CoursesConfig(_Section):
    """Course import (docs/02 §10, docs/04 §9)."""

    identifier_labels: IdentifierLabels
    date_labels: IdentifierLabels  # labels only before a colon or a date ("Born: …"; DECISIONS #89)
    born_date_max_words: int = Field(ge=0)  # a date within this many words after a date label goes (#100)
    month_names: IdentifierLabels
    allowed_types: list[str]
    max_upload_bytes: int = Field(gt=0)
    max_unpacked_bytes: int = Field(gt=0)
    min_pdf_text_per_page: int = Field(ge=0)
    letter_grade_order: list[str]
    numeric_grade_scale: float = Field(gt=0)
    removed_marker: str
    grade_headings: list[str]  # heading-row cells that name the grade column (row check, #67)
    credit_headings: list[str]  # heading-row cells that name the credits column
    term_words: list[str]  # "block 1", "semester 2": never a grade, nor are years (#80)
    term_qualifiers: list[str]  # "1e", "Fall" before a term word: the number after it is no term (#81)


class WrongSchoolConfig(_Section):
    own_background_terms: list[str]
    extra_terms: list[str]
    allow_phrases: list[str]


class GoogleConfig(_Section):
    calendar_name: str
    scopes: list[str]
    client_secret_path: str
    token_path: str
    timeout_s: int
    max_retries: int
    backoff_s: list[float]


class DigestConfig(_Section):
    lookahead_days: int
    waiting_days: int
    unverified_window_days: int
    send_when_empty: bool


class ScheduleConfig(_Section):
    sync_logon_delay_minutes: int
    digest_logon_delay_minutes: int
    sync_daily_time: str


class PerformanceConfig(_Section):
    sheet_professors_benchmark: int
    test_workers: int = Field(ge=1)  # pytest-xdist workers used by tasks.py test-fast and test
    test_fast_warn_seconds: float = Field(gt=0)  # tasks.py warns above this (C-0-02 limit is 60 s)


class Config(_Section):
    """The whole of ``config/project.yaml`` plus the repository paths derived from it."""

    storage: StorageConfig
    app: AppConfig
    time: TimeConfig
    urgency: UrgencyConfig
    letters: LettersConfig
    reminders: RemindersConfig
    email: EmailConfig
    claude: ClaudeConfig
    evaluation: EvaluationConfig
    research: ResearchConfig
    standard_items: StandardItemsConfig
    names: NamesConfig
    courses: CoursesConfig
    wrong_school: WrongSchoolConfig
    google: GoogleConfig
    digest: DigestConfig
    schedule: ScheduleConfig
    performance: PerformanceConfig

    repo_root: Path = REPO_ROOT

    @property
    def seed_dir(self) -> Path:
        """Folder holding the starting programmes copied by ``hub init-data``."""
        return self.repo_root / "seed" / "programmes"

    @property
    def display_zone_ids(self) -> list[str]:
        return [z.tz for z in self.time.display_zones]


def load_config(path: Path | None = None, env: Mapping[str, str] | None = None) -> Config:
    """Read and validate the project config; apply the ``HUB_DATA_DIR`` override.

    Raises ``ValueError`` naming the offending key when the file does not match the expected shape.
    """
    path = path or DEFAULT_CONFIG_PATH
    env = os.environ if env is None else env
    raw = YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
    override = env.get(DATA_DIR_ENV)
    if override:
        raw.setdefault("storage", {})["data_dir"] = override
    claude_path = env.get(CLAUDE_PATH_ENV)
    if claude_path:
        raw.setdefault("claude", {})["path"] = claude_path
    try:
        cfg = Config.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise ValueError(f"{path.name}: {where}: {first['msg']}") from exc
    data_dir = cfg.storage.data_dir
    if not data_dir.is_absolute():
        data_dir = (cfg.repo_root / data_dir).resolve()
    return cfg.model_copy(update={"storage": cfg.storage.model_copy(update={"data_dir": data_dir})})
