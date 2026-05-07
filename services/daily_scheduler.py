from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

DAILY_UTC_HOUR = 13
DAILY_UTC_MINUTE = 0


def is_enabled() -> bool:
    explicit = os.getenv("DASHBOARD_DAILY_SCHEDULER_ENABLED")
    if explicit is not None:
        return explicit.strip().lower() in {"1", "true", "yes", "on"}
    return os.getenv("RAILWAY_SERVICE_NAME") == "leadgen"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def seconds_until_next_utc_time(now: datetime, *, hour: int, minute: int) -> int:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    target = datetime.combine(
        now.date(),
        time(hour=hour, minute=minute, tzinfo=timezone.utc),
    )
    if target <= now:
        target += timedelta(days=1)
    return int((target - now).total_seconds())


async def run_daily_once() -> int:
    script = Path(__file__).resolve().parent.parent / "jobs" / "run_daily.py"
    log.info("Starting scheduled daily scrape subprocess: %s", script)
    process = await asyncio.create_subprocess_exec(sys.executable, str(script))
    return_code = await process.wait()
    if return_code:
        log.warning("Scheduled daily scrape exited with code %s", return_code)
    else:
        log.info("Scheduled daily scrape completed")
    return return_code


async def run_forever() -> None:
    while True:
        delay = seconds_until_next_utc_time(
            _utc_now(),
            hour=DAILY_UTC_HOUR,
            minute=DAILY_UTC_MINUTE,
        )
        log.info("Next daily scrape scheduled in %ss", delay)
        await asyncio.sleep(delay)
        await run_daily_once()
