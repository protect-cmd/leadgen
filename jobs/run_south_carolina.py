import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers.south_carolina.richland import RichlandSCScraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


async def main() -> None:
    log.info("Starting South Carolina scrape job")

    scrapers = [
        ("Richland County", RichlandSCScraper(lookback_days=7)),
    ]

    for label, scraper in scrapers:
        log.info(f"=== South Carolina / {label} ===")
        filings = await scraper.scrape()
        if filings:
            log.info(f"{label}: {len(filings)} filings")
            for f in filings[:5]:
                log.info(
                    f"  {f.case_number} | {f.tenant_name} | {f.filing_date} | {f.property_address[:40]}"
                )
        else:
            log.info(f"{label}: no filings found")

    log.info("South Carolina scrape job complete")


if __name__ == "__main__":
    asyncio.run(main())
