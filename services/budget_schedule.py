"""Calendar-based per-business enrichment budget + weekend pause.

From the operator's trend analysis: lead strength varies by day-of-month, so the
daily enrichment budget is tiered. SearchBug bills $1 per SUCCESSFUL lookup (a
number returned); no-hits are free. So the cap counts PAID hits per day: the
quota commits a slot only on a hit and rolls back no-hits, letting the pipeline
keep trying until it lands the day's number of paid leads. Each business gets the
tier's cap independently. Weekends (Philippine
time) pause ALL paid actions (enrich / GHL / Bland) — scraping still runs, but
nothing goes live, so leads aren't burned on a day we don't work them.

Tiers by PDT day-of-month:
    green  06-17  strongest leads  -> $125/business/day
    red    18-28  weakest leads    -> $35/business/day
    yellow 29-05  middle           -> $75/business/day
Day 28 resolves to RED (the lower budget) per "$35 during 18 to 28"; flip the
boundary below if you meant 28 to be yellow. Caps are env-overridable.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

_PDT = timezone(timedelta(hours=-7))   # Pacific (schedule is defined in PDT)
_PHT = timezone(timedelta(hours=8))    # Philippine (weekend pause)

GREEN, YELLOW, RED = "green", "yellow", "red"

_TIER_CAPS = {
    GREEN: int(os.getenv("QUOTA_BUDGET_GREEN", "125")),
    YELLOW: int(os.getenv("QUOTA_BUDGET_YELLOW", "75")),
    RED: int(os.getenv("QUOTA_BUDGET_RED", "35")),
}


def tier_for_date(d: date) -> str:
    """Trend-analysis tier for a PDT calendar date (by day-of-month)."""
    dom = d.day
    if 6 <= dom <= 17:
        return GREEN
    if 18 <= dom <= 28:
        return RED
    return YELLOW  # 29, 30, 31, 1, 2, 3, 4, 5


def _today_pdt() -> date:
    return datetime.now(_PDT).date()


def enrichment_cap(day: date | None = None) -> int:
    """Per-business daily enrichment cap for the day's tier (PDT)."""
    return _TIER_CAPS[tier_for_date(day or _today_pdt())]


def is_weekend_pht(now: datetime | None = None) -> bool:
    """True on Saturday/Sunday in Philippine time — paid actions pause."""
    n = now or datetime.now(timezone.utc)
    if n.tzinfo is None:
        n = n.replace(tzinfo=timezone.utc)
    return n.astimezone(_PHT).weekday() >= 5  # 5=Sat, 6=Sun


def paid_actions_paused(now: datetime | None = None) -> bool:
    """Whether paid actions (enrich/GHL/Bland) are paused right now. Currently
    just the weekend (PHT) pause; opt-out with WEEKEND_PAUSE_ENABLED=false."""
    if os.getenv("WEEKEND_PAUSE_ENABLED", "true").lower() != "true":
        return False
    return is_weekend_pht(now)
