from __future__ import annotations

from datetime import datetime, timezone

import pytest


def test_seconds_until_tennessee_window_waits_until_1320_utc():
    from jobs.run_daily import seconds_until_utc_time

    now = datetime(2026, 5, 7, 13, 5, 0, tzinfo=timezone.utc)

    assert seconds_until_utc_time(now, hour=13, minute=20) == 900


def test_seconds_until_tennessee_window_does_not_wait_after_1320_utc():
    from jobs.run_daily import seconds_until_utc_time

    now = datetime(2026, 5, 7, 13, 25, 0, tzinfo=timezone.utc)

    assert seconds_until_utc_time(now, hour=13, minute=20) == 0


@pytest.mark.asyncio
async def test_daily_job_runs_all_scheduled_jobs_in_order(monkeypatch):
    from jobs import run_daily
    from services.daily_scheduler import ScheduledJob

    calls: list[tuple[str, tuple[str, ...]] | tuple[str, int]] = []

    async def fake_sleep(seconds: int) -> None:
        calls.append(("sleep", seconds))

    async def fake_run_script_once(script_name: str, args: tuple[str, ...] = ()) -> int:
        calls.append((script_name, args))
        return 0

    monkeypatch.setattr(
        run_daily.daily_scheduler,
        "SCHEDULED_JOBS",
        (
            ScheduledJob("texas", 13, 0, "run_texas.py"),
            ScheduledJob("tennessee", 13, 20, "run_tennessee.py"),
            ScheduledJob("arizona", 13, 40, "run_arizona.py", args=("--pipe", "--notify")),
            ScheduledJob("georgia_cobb", 14, 0, "run_georgia_cobb.py", args=("--pipe", "--notify")),
        ),
    )
    monkeypatch.setattr(run_daily.daily_scheduler, "run_script_once", fake_run_script_once)
    monkeypatch.setattr(run_daily.asyncio, "sleep", fake_sleep)
    now_values = iter(
        [
            datetime(2026, 5, 7, 12, 40, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 7, 13, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 7, 13, 20, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 7, 13, 40, 0, tzinfo=timezone.utc),
        ]
    )
    monkeypatch.setattr(run_daily, "_utc_now", lambda: next(now_values))

    await run_daily.main()

    assert calls == [
        ("sleep", 1200),
        ("run_texas.py", ()),
        ("sleep", 1200),
        ("run_tennessee.py", ()),
        ("sleep", 1200),
        ("run_arizona.py", ("--pipe", "--notify")),
        ("sleep", 1200),
        ("run_georgia_cobb.py", ("--pipe", "--notify")),
    ]


@pytest.mark.asyncio
async def test_daily_job_continues_after_scheduled_script_failure(monkeypatch):
    from jobs import run_daily
    from services.daily_scheduler import ScheduledJob

    calls: list[str] = []

    async def fake_sleep(seconds: int) -> None:
        calls.append(f"sleep:{seconds}")

    async def fake_run_script_once(script_name: str, args: tuple[str, ...] = ()) -> int:
        calls.append(script_name)
        return 1 if script_name == "run_texas.py" else 0

    monkeypatch.setattr(
        run_daily.daily_scheduler,
        "SCHEDULED_JOBS",
        (
            ScheduledJob("texas", 13, 0, "run_texas.py"),
            ScheduledJob("tennessee", 13, 20, "run_tennessee.py"),
        ),
    )
    monkeypatch.setattr(run_daily.daily_scheduler, "run_script_once", fake_run_script_once)
    monkeypatch.setattr(run_daily.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(
        run_daily,
        "_utc_now",
        lambda: datetime(2026, 5, 7, 13, 25, 0, tzinfo=timezone.utc),
    )

    await run_daily.main()

    assert calls == ["run_texas.py", "run_tennessee.py"]
