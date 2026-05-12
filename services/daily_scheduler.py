from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 30
DEFAULT_CATCH_UP_SECONDS = 3600


@dataclass(frozen=True)
class ScheduledJob:
    name: str
    hour: int
    minute: int
    script_name: str


SCHEDULED_JOBS: tuple[ScheduledJob, ...] = (
    ScheduledJob("texas", 13, 0, "run_texas.py"),
    ScheduledJob("tennessee", 13, 20, "run_tennessee.py"),
)


def is_enabled() -> bool:
    explicit = os.getenv("DASHBOARD_DAILY_SCHEDULER_ENABLED")
    if explicit is not None:
        return explicit.strip().lower() in {"1", "true", "yes", "on"}
    return os.getenv("RAILWAY_SERVICE_NAME") == "leadgen"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _catch_up_seconds() -> int:
    raw = os.getenv("DASHBOARD_DAILY_SCHEDULER_CATCH_UP_SECONDS", "")
    if not raw:
        return DEFAULT_CATCH_UP_SECONDS
    try:
        return max(0, int(raw))
    except ValueError:
        log.warning("Invalid DASHBOARD_DAILY_SCHEDULER_CATCH_UP_SECONDS=%r", raw)
        return DEFAULT_CATCH_UP_SECONDS


def _target_for_date(now: datetime, *, hour: int, minute: int) -> datetime:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    return datetime.combine(
        now.date(),
        time(hour=hour, minute=minute, tzinfo=timezone.utc),
    )


def seconds_until_next_utc_time(now: datetime, *, hour: int, minute: int) -> int:
    target = _target_for_date(now, hour=hour, minute=minute)
    if target <= now:
        target += timedelta(days=1)
    return int((target - now).total_seconds())


def is_due_for_catch_up(
    now: datetime,
    *,
    hour: int,
    minute: int,
    catch_up_seconds: int | None = None,
) -> bool:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    target = _target_for_date(now, hour=hour, minute=minute)
    elapsed = (now - target).total_seconds()
    return 0 <= elapsed <= (catch_up_seconds if catch_up_seconds is not None else _catch_up_seconds())


async def run_script_once(script_name: str) -> int:
    script = Path(__file__).resolve().parent.parent / "jobs" / script_name
    log.info("Starting scheduled scrape subprocess: %s", script)
    process = await asyncio.create_subprocess_exec(sys.executable, str(script))
    return_code = await process.wait()
    if return_code:
        log.warning("Scheduled scrape %s exited with code %s", script_name, return_code)
    else:
        log.info("Scheduled scrape %s completed", script_name)
    return return_code


async def run_daily_once() -> int:
    """Run both daily state jobs sequentially for manual/backward-compatible use."""
    return_code = 0
    for job in SCHEDULED_JOBS:
        return_code = max(return_code, await run_script_once(job.script_name))
    return return_code


async def run_forever() -> None:
    started_dates: set[tuple[str, str]] = set()
    while True:
        now = _utc_now()
        due_jobs = [
            job
            for job in SCHEDULED_JOBS
            if is_due_for_catch_up(now, hour=job.hour, minute=job.minute)
            and (job.name, now.date().isoformat()) not in started_dates
        ]
        if due_jobs:
            for job in due_jobs:
                started_dates.add((job.name, now.date().isoformat()))
                log.info("Starting scheduled %s scrape", job.name)
                await run_script_once(job.script_name)
            continue

        next_delay = min(
            seconds_until_next_utc_time(now, hour=job.hour, minute=job.minute)
            for job in SCHEDULED_JOBS
        )
        sleep_for = min(CHECK_INTERVAL_SECONDS, max(1, next_delay))
        log.info("Next scheduled scrape check in %ss", sleep_for)
        await asyncio.sleep(sleep_for)
