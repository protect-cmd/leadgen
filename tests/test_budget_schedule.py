"""Tests for the calendar budget schedule + weekend pause."""
from __future__ import annotations

from datetime import date, datetime, timezone

import services.budget_schedule as bs


def test_tier_for_date_ranges():
    assert bs.tier_for_date(date(2026, 6, 10)) == bs.GREEN   # 6-17
    assert bs.tier_for_date(date(2026, 6, 17)) == bs.GREEN
    assert bs.tier_for_date(date(2026, 6, 18)) == bs.RED     # 18-28
    assert bs.tier_for_date(date(2026, 6, 28)) == bs.RED     # boundary -> red (lower budget)
    assert bs.tier_for_date(date(2026, 6, 29)) == bs.YELLOW  # 29-05
    assert bs.tier_for_date(date(2026, 6, 5)) == bs.YELLOW
    assert bs.tier_for_date(date(2026, 6, 1)) == bs.YELLOW


def test_enrichment_cap_per_tier_defaults():
    assert bs.enrichment_cap(date(2026, 6, 10)) == 125   # green
    assert bs.enrichment_cap(date(2026, 6, 20)) == 35    # red
    assert bs.enrichment_cap(date(2026, 6, 30)) == 75    # yellow


def test_is_weekend_pht_converts_timezone():
    # Jan 3 2026 is a Saturday. 08:00 PHT that day -> weekend.
    assert bs.is_weekend_pht(datetime(2026, 1, 3, 0, 0, tzinfo=timezone.utc)) is True
    # Fri 18:00 UTC is already Sat 02:00 in PHT (+8) -> weekend.
    assert bs.is_weekend_pht(datetime(2026, 1, 2, 18, 0, tzinfo=timezone.utc)) is True
    # Jan 5 2026 is a Monday.
    assert bs.is_weekend_pht(datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)) is False


def test_paid_actions_paused_respects_optout(monkeypatch):
    sat = datetime(2026, 1, 3, 0, 0, tzinfo=timezone.utc)
    monkeypatch.delenv("WEEKEND_PAUSE_ENABLED", raising=False)
    assert bs.paid_actions_paused(sat) is True
    monkeypatch.setenv("WEEKEND_PAUSE_ENABLED", "false")
    assert bs.paid_actions_paused(sat) is False
