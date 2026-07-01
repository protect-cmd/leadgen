"""Railway cron entry point for Indiana eviction judgment scraping (ISTS client)."""
from __future__ import annotations
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


async def main() -> None:
    from pipeline.runner import run
    from scrapers.indiana.mycase_ists import IndianaISTSScraper
    from services import notification_service

    log.info("Starting Indiana ISTS scrape job")

    counties = [
        ("Indiana", IndianaISTSScraper()),   # defaults: lookback_days=25, judgment_recency_days=7
    ]

    for name, scraper in counties:
        log.info(f"=== Indiana ISTS / {name} ===")
        try:
            filings = await scraper.scrape()
            if scraper.last_error:
                log.error(f"{name}: scrape error: {scraper.last_error}")
                await notification_service.send_job_error(
                    job=f"Indiana ISTS / {name}",
                    stage="scrape",
                    error=scraper.last_error,
                )
            if not filings:
                log.info(f"{name}: no filings found")
                continue
            await run(filings, state="IN", county=name)
        except NotImplementedError as e:
            log.warning(f"{name}: scraper not yet implemented - {e}")
        except Exception as e:
            log.error(f"{name}: unexpected error - {e}", exc_info=True)

    log.info("Indiana ISTS scrape job complete")


if __name__ == "__main__":
    asyncio.run(main())
