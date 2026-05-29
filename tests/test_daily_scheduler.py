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


def test_scheduler_catches_up_recently_missed_state_window():
    from services.daily_scheduler import is_due_for_catch_up

    now = datetime(2026, 5, 12, 13, 21, 0, tzinfo=timezone.utc)

    assert is_due_for_catch_up(now, hour=13, minute=20, catch_up_seconds=3600)


def test_scheduler_does_not_catch_up_old_state_window():
    from services.daily_scheduler import is_due_for_catch_up

    now = datetime(2026, 5, 12, 15, 1, 0, tzinfo=timezone.utc)

    assert not is_due_for_catch_up(now, hour=13, minute=20, catch_up_seconds=3600)


def test_scheduler_defines_daily_jobs():
    """Tarrant + georgia_cobb descheduled 2026-05-29 (Spec 2). See
    docs/superpowers/specs/2026-05-29-tarrant-rebuild-design.md and
    docs/superpowers/specs/2026-05-29-cobb-address-enrichment-rebuild-design.md."""
    from services import daily_scheduler

    assert [(job.name, job.hour, job.minute, job.script_name) for job in daily_scheduler.SCHEDULED_JOBS] == [
        ("texas", 13, 0, "run_texas.py"),
        ("tennessee", 13, 20, "run_tennessee.py"),
        ("arizona", 13, 40, "run_arizona.py"),
        ("ohio_franklin_raw", 14, 20, "../scripts/push_franklin_filings.py"),
        ("ohio_hamilton", 14, 40, "run_ohio.py"),
    ]
    az_job = next(j for j in daily_scheduler.SCHEDULED_JOBS if j.name == "arizona")
    assert "--pipe" in az_job.args
    assert "--notify" in az_job.args


def test_tarrant_descheduled():
    """Spec 2b: Bright Data tunnel failures (ERR_TUNNEL_CONNECTION_FAILED)."""
    from services.daily_scheduler import SCHEDULED_JOBS
    assert "tarrant" not in [j.name for j in SCHEDULED_JOBS]


def test_georgia_cobb_descheduled():
    """Spec 2c: 4% gate pass rate due to Nominatim flakiness in address chain."""
    from services.daily_scheduler import SCHEDULED_JOBS
    assert "georgia_cobb" not in [j.name for j in SCHEDULED_JOBS]


def test_ohio_franklin_job_is_raw_supabase_only():
    from services.daily_scheduler import SCHEDULED_JOBS

    job = next(j for j in SCHEDULED_JOBS if j.name == "ohio_franklin_raw")

    assert job.args == ("--lookback-days", "2", "--yes-write-supabase", "--notify")
    assert "--pipe" not in job.args


def test_ohio_hamilton_job_is_scheduled_for_pipeline():
    from services.daily_scheduler import SCHEDULED_JOBS

    job = next(j for j in SCHEDULED_JOBS if j.name == "ohio_hamilton")

    assert job.script_name == "run_ohio.py"
    assert job.args == (
        "--lookback-days",
        "2",
        "--counties",
        "hamilton",
        "--pipe",
        "--notify",
    )
