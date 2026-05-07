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
async def test_daily_job_runs_texas_then_waits_then_tennessee(monkeypatch):
    from jobs import run_daily

    calls: list[tuple[str, int | None]] = []

    async def fake_texas_main() -> None:
        calls.append(("texas", None))

    async def fake_tennessee_main() -> None:
        calls.append(("tennessee", None))

    async def fake_sleep(seconds: int) -> None:
        calls.append(("sleep", seconds))

    monkeypatch.setattr(run_daily.run_texas, "main", fake_texas_main)
    monkeypatch.setattr(run_daily.run_tennessee, "main", fake_tennessee_main)
    monkeypatch.setattr(run_daily.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(
        run_daily,
        "_utc_now",
        lambda: datetime(2026, 5, 7, 13, 5, 0, tzinfo=timezone.utc),
    )

    await run_daily.main()

    assert calls == [("texas", None), ("sleep", 900), ("tennessee", None)]


@pytest.mark.asyncio
async def test_daily_job_continues_to_tennessee_if_texas_fails(monkeypatch):
    from jobs import run_daily

    calls: list[str] = []

    async def fake_texas_main() -> None:
        calls.append("texas")
        raise RuntimeError("portal failed")

    async def fake_tennessee_main() -> None:
        calls.append("tennessee")

    async def fake_sleep(seconds: int) -> None:
        calls.append(f"sleep:{seconds}")

    async def fake_send_job_error(**kwargs) -> None:
        calls.append(f"alert:{kwargs['stage']}")

    monkeypatch.setattr(run_daily.run_texas, "main", fake_texas_main)
    monkeypatch.setattr(run_daily.run_tennessee, "main", fake_tennessee_main)
    monkeypatch.setattr(run_daily.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(run_daily.notification_service, "send_job_error", fake_send_job_error)
    monkeypatch.setattr(
        run_daily,
        "_utc_now",
        lambda: datetime(2026, 5, 7, 13, 25, 0, tzinfo=timezone.utc),
    )

    await run_daily.main()

    assert calls == ["texas", "alert:texas", "tennessee"]
