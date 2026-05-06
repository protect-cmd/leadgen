"""Railway cron entry point for Texas eviction scraping."""
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
    from scrapers.texas.harris import HarrisCountyScraper

    log.info("Starting Texas scrape job")

    counties = [
        ("Harris", HarrisCountyScraper(lookback_days=2)),
    ]

    for name, scraper in counties:
        log.info(f"=== Texas / {name} County ===")
        try:
            filings = await scraper.scrape()
            if not filings:
                log.info(f"{name}: no filings found (normal if no cases filed today)")
                continue
            await run(filings)
        except NotImplementedError as e:
            log.warning(f"{name}: scraper not yet implemented — {e}")
        except Exception as e:
            log.error(f"{name}: unexpected error — {e}", exc_info=True)

    log.info("Texas scrape job complete")


if __name__ == "__main__":
    asyncio.run(main())
