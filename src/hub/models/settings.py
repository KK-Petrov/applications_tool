"""Owner settings: ``settings.yaml`` (docs/01 §2)."""

from __future__ import annotations

from hub.models.common import DataModel, Email, IanaZone


class Settings(DataModel):
    """Owner details and display choices; ``owner_email`` is the only address the digest may go to."""

    owner_name: str
    owner_email: Email
    display_tz: IanaZone  # must also be one of config time.display_zones (checked by the store)
    reminder_tz: IanaZone
    calendar_id: str | None = None
    google_connected: bool = False
