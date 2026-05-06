import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers.tennessee.davidson import DavidsonTNScraper
from pipeline import runner
from services import notification_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


async def main() -> None:
    log.info("Starting Tennessee scrape job")

    scrapers = [
        ("Davidson County", DavidsonTNScraper(lookback_days=2)),
    ]

    for label, scraper in scrapers:
        log.info(f"=== Tennessee / {label} ===")
        filings = scraper.scrape()
        log.info(f"{label}: {len(filings)} filings scraped")
        if scraper.last_error:
            await notification_service.send_job_error(
                job=f"Tennessee / {label}",
                stage="scrape",
                error=scraper.last_error,
            )
        if filings:
            await runner.run(filings, state="TN", county=label)
        else:
            log.info(f"{label}: no filings found")

    log.info("Tennessee scrape job complete")


if __name__ == "__main__":
    asyncio.run(main())
