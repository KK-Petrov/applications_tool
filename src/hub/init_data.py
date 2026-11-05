"""``hub init-data``: prepare an empty data folder (docs/01 §9).

Copies the seed programmes into ``programmes/`` only when that folder holds no programme yet (IDs are
never reused, so a programme the user deleted on purpose must not come back), and creates
``settings.yaml`` from the owner's name and email. It never overwrites an existing file.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError

from hub.config import Config
from hub.models import Settings
from hub.store import PROFESSORS_DIR, PROGRAMMES_DIR, SETTINGS_FILE, DataError, Store

FOLDERS = (PROGRAMMES_DIR, PROFESSORS_DIR, "logs")


@dataclass
class InitReport:
    """What init-data did, so the CLI can say it plainly."""

    programmes_copied: list[str] = field(default_factory=list)
    programmes_skipped_reason: str | None = None
    settings_created: bool = False


def settings_exist(config: Config) -> bool:
    return (config.storage.data_dir / SETTINGS_FILE).exists()


def init_data(config: Config, owner_name: str | None, owner_email: str | None) -> InitReport:
    """Create folders, copy seeds and write settings.yaml where missing.

    ``owner_name``/``owner_email`` are only used when settings.yaml does not exist yet; an invalid
    email raises ``ValueError`` before anything is written.
    """
    data_dir = config.storage.data_dir
    report = InitReport()
    settings: Settings | None = None
    if not settings_exist(config):
        try:
            settings = Settings(
                owner_name=(owner_name or "").strip(),
                owner_email=(owner_email or "").strip(),
                display_tz=config.time.default_display_tz,
                reminder_tz=config.time.default_reminder_tz,
                calendar_id=None,  # passed explicitly so every docs/01 §2 field is written
                google_connected=False,
            )
        except ValidationError as exc:
            first = exc.errors()[0]
            raise ValueError(f"{first['loc'][0]}: {first['msg'].removeprefix('Value error, ')}") from None
        if not settings.owner_name:
            raise ValueError("owner_name: must not be empty")

    programmes = data_dir / PROGRAMMES_DIR
    copy_seeds = not (programmes.is_dir() and any(programmes.glob("*.yaml")))
    seeds = sorted(config.seed_dir.glob("*.yaml")) if copy_seeds else []
    seed_store = Store(config.seed_dir.parent, config)
    for seed in seeds:  # validate every seed before writing anything
        try:
            seed_store.load_programme(seed)
        except DataError as err:
            raise ValueError(f"seed file {seed.name} is invalid: {err}") from None

    for folder in FOLDERS:
        (data_dir / folder).mkdir(parents=True, exist_ok=True)
    store = Store.from_config(config)
    if not copy_seeds:
        report.programmes_skipped_reason = "programmes/ already holds programme files"
    for seed in seeds:
        target = programmes / seed.name
        if not target.exists():
            store.write_bytes_atomic(target, seed.read_bytes())  # byte for byte, comments included
            report.programmes_copied.append(seed.name)

    if settings is not None:
        store.save_settings(settings)
        report.settings_created = True
    return report
