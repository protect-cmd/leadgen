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

from services import daily_scheduler

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)

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

    for job in daily_scheduler.SCHEDULED_JOBS:
        delay = seconds_until_utc_time(_utc_now(), hour=job.hour, minute=job.minute)
        if delay:
            log.info("Waiting %ss for %s scrape window", delay, job.name)
            await asyncio.sleep(delay)

        return_code = await daily_scheduler.run_script_once(job.script_name, job.args)
        if return_code:
            log.warning("%s scrape job exited with code %s", job.name, return_code)

    log.info("Daily scrape job complete")

if __name__ == "__main__":
    asyncio.run(main())
