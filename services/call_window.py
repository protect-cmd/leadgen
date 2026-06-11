"""Shared TCPA calling-hours gate.

Outbound dials must land within the LEAD's local time window. Federal TCPA safe
harbor is 8am-9pm local; tighten via env (CALL_WINDOW_START_HOUR / _END_HOUR).
We also block Sunday before 10am, mirroring the ISTS W1 policy.

Used by the Vantage fire path (fire_service.fire_case + bland_service). The ISTS
path keeps its own stricter W1 window in services/ists_bland.py.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


class OutsideCallWindow(RuntimeError):
    """Raised when a dial is attempted outside the lead's local calling window."""


# State -> IANA timezone for every state the scrapers currently cover. Unknown
# states fall back to Central (conservative: Central 8am is Eastern 9am, etc.).
_STATE_TZ = {
    "TX": "America/Chicago", "TN": "America/Chicago", "IL": "America/Chicago",
    "OH": "America/New_York", "GA": "America/New_York", "FL": "America/New_York",
    "IN": "America/Indiana/Indianapolis",
    "AZ": "America/Phoenix", "CO": "America/Denver",
    "CA": "America/Los_Angeles", "WA": "America/Los_Angeles", "NV": "America/Los_Angeles",
}
_FALLBACK_TZ = "America/Chicago"


def tz_for_state(state: str | None) -> ZoneInfo:
    return ZoneInfo(_STATE_TZ.get((state or "").strip().upper(), _FALLBACK_TZ))


def _window() -> tuple[int, int]:
    start = int(os.getenv("CALL_WINDOW_START_HOUR", "8"))
    end = int(os.getenv("CALL_WINDOW_END_HOUR", "21"))
    return start, end


def in_call_window(state: str | None, now_utc: datetime | None = None) -> bool:
    """True if it is currently within the legal calling window for `state`."""
    now_utc = now_utc or datetime.now(timezone.utc)
    local = now_utc.astimezone(tz_for_state(state))
    start, end = _window()
    if local.weekday() == 6 and local.hour < 10:  # Sunday before 10am
        return False
    return start <= local.hour < end
