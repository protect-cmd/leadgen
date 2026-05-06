from __future__ import annotations

from datetime import datetime, timezone

from scrapers.dates import court_today


def test_court_today_uses_requested_timezone():
    now_utc = datetime(2026, 5, 7, 2, 30, tzinfo=timezone.utc)

    assert court_today("America/Los_Angeles", now_utc=now_utc).isoformat() == "2026-05-06"
    assert court_today("America/Chicago", now_utc=now_utc).isoformat() == "2026-05-06"
    assert court_today("Asia/Manila", now_utc=now_utc).isoformat() == "2026-05-07"


def test_court_today_treats_naive_datetimes_as_utc():
    now_utc = datetime(2026, 5, 7, 2, 30)

    assert court_today("America/New_York", now_utc=now_utc).isoformat() == "2026-05-06"
