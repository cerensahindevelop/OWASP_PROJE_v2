"""Localize UTC database/API timestamps only at the presentation boundary."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


TURKEY_TIMEZONE = ZoneInfo("Europe/Istanbul")


def format_turkey_time(
    value: datetime | None,
    fmt: str = "%d.%m.%Y %H:%M:%S",
    *,
    missing: str = "—",
) -> str:
    if value is None:
        return missing
    # SQLite CURRENT_TIMESTAMP is UTC but its datetime values have no offset.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(TURKEY_TIMEZONE).strftime(fmt)
