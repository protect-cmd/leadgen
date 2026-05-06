from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


def court_today(timezone_name: str, *, now_utc: datetime | None = None) -> date:
    """Return today's date in the court portal's local timezone."""
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(ZoneInfo(timezone_name)).date()
