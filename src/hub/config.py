"""Typed access to ``config/project.yaml``, the single home of every tunable parameter.

``HUB_DATA_DIR`` (environment variable) overrides ``storage.data_dir``; tests always set it so they
never touch the owner's real data folder.
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


class RemindersConfig(_Section):
    letters_request_days: int
    documents_days: int
    letters_check_days: int
    essay_final_days: int
    deadline_alert_days: list[int]
    interview_eve_days: int
    time_of_day: str
    rule_event_minutes: int
    deadline_popups_minutes: list[int]
    interview_popups_minutes: list[int]


class EmailConfig(_Section):
    min_words: int
    max_words: int
    subject_template: str
    sign_off: str


class ClaudeConfig(_Section):
    path: str | None
    timeout_s: int
    research_timeout_s: int
    usage_log: str


class EvaluationConfig(_Section):
    ratings: list[str]
    criteria: list[str]
    max_comment_words: int


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
