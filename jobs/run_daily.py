"""Railway cron entry point for the daily multi-state scrape."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, time, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from jobs import run_tennessee, run_texas
from services import notification_service

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)

TENNESSEE_UTC_HOUR = 13
TENNESSEE_UTC_MINUTE = 20


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def seconds_until_utc_time(now: datetime, *, hour: int, minute: int) -> int:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    target = datetime.combine(
        now.date(),
        time(hour=hour, minute=minute, tzinfo=timezone.utc),
    )
    return max(0, int((target - now).total_seconds()))


async def main() -> None:
    log.info("Starting daily scrape job")

    await _run_state_job("Texas", "texas", run_texas.main)

    delay = seconds_until_utc_time(
        _utc_now(),
        hour=TENNESSEE_UTC_HOUR,
        minute=TENNESSEE_UTC_MINUTE,
    )
    if delay:
        log.info("Waiting %ss for Tennessee scrape window", delay)
        await asyncio.sleep(delay)

    await _run_state_job("Tennessee", "tennessee", run_tennessee.main)

    log.info("Daily scrape job complete")


async def _run_state_job(label: str, stage: str, job_main) -> None:
    try:
        await job_main()
    except Exception as e:
        log.error("%s scrape job failed: %s", label, e, exc_info=True)
        await notification_service.send_job_error(
            job=f"Daily scrape / {label}",
            stage=stage,
            error=e,
        )


if __name__ == "__main__":
    asyncio.run(main())
