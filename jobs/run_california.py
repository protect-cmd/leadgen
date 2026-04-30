from __future__ import annotations
import asyncio
import logging
import os
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

log = logging.getLogger(__name__)


async def main() -> None:
    from scrapers.california.los_angeles import LosAngelesScraper
    from pipeline.runner import run

    log.info("Starting California scrape job")

    scrapers = [
        ("Los Angeles", LosAngelesScraper()),
    ]

    for county, scraper in scrapers:
        log.info(f"Scraping {county} County")
        try:
            filings = await scraper.scrape()
            log.info(f"{county}: {len(filings)} filings scraped")
            await run(filings)
        except NotImplementedError as e:
            log.warning(f"{county} scraper not yet implemented: {e}")
        except Exception as e:
            log.error(f"{county} scrape failed: {e}", exc_info=True)

    log.info("California scrape job complete")


if __name__ == "__main__":
    asyncio.run(main())
