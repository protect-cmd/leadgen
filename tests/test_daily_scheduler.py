from __future__ import annotations

from datetime import datetime, timezone


def test_scheduler_enables_by_default_on_railway_leadgen_service(monkeypatch):
    from services import daily_scheduler

    monkeypatch.delenv("DASHBOARD_DAILY_SCHEDULER_ENABLED", raising=False)
    monkeypatch.setenv("RAILWAY_SERVICE_NAME", "leadgen")

    assert daily_scheduler.is_enabled()


def test_scheduler_can_be_disabled_explicitly(monkeypatch):
    from services import daily_scheduler

    monkeypatch.setenv("DASHBOARD_DAILY_SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("RAILWAY_SERVICE_NAME", "leadgen")

    assert not daily_scheduler.is_enabled()


def test_seconds_until_next_daily_run_uses_today_before_schedule():
    from services.daily_scheduler import seconds_until_next_utc_time

    now = datetime(2026, 5, 7, 12, 55, 0, tzinfo=timezone.utc)

    assert seconds_until_next_utc_time(now, hour=13, minute=0) == 300


def test_seconds_until_next_daily_run_uses_tomorrow_after_schedule():
    from services.daily_scheduler import seconds_until_next_utc_time

    now = datetime(2026, 5, 7, 13, 5, 0, tzinfo=timezone.utc)

    assert seconds_until_next_utc_time(now, hour=13, minute=0) == 86100
