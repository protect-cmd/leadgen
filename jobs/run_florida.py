import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers.florida.miami_dade import MiamiDadeScraper
from scrapers.florida.broward import BrowardScraper
from scrapers.florida.hillsborough import HillsboroughScraper
from pipeline import runner
from services import notification_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


async def main() -> None:
    log.info("Starting Florida scrape job")

    scrapers = [
        ("Miami-Dade County", MiamiDadeScraper(lookback_days=2)),
        ("Broward County", BrowardScraper(lookback_days=2)),
        ("Hillsborough County", HillsboroughScraper(lookback_days=2)),
    ]

    for label, scraper in scrapers:
        log.info(f"=== Florida / {label} ===")
        try:
            filings = await scraper.scrape()
        except Exception as e:
            log.error(f"Florida / {label}: unexpected error: {e}", exc_info=True)
            await notification_service.send_job_error(
                job=f"Florida / {label}",
                stage="scrape",
                error=str(e),
            )
            continue

        log.info(f"{label}: {len(filings)} filings scraped")

        if filings:
            await runner.run(filings, state="FL", county=label)
        else:
            log.info(f"{label}: no filings found")

    log.info("Florida scrape job complete")


if __name__ == "__main__":
    asyncio.run(main())
