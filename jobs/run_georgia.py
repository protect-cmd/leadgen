import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers.georgia.researchga import ReSearchGAScraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


async def main() -> None:
    log.info("Starting Georgia scrape job")

    scrapers = [
        ("re:SearchGA", ReSearchGAScraper(lookback_days=2)),
    ]

    for label, scraper in scrapers:
        log.info(f"=== Georgia / {label} ===")
        filings = await scraper.scrape()
        if filings:
            log.info(f"{label}: {len(filings)} filings")
            for f in filings[:5]:
                log.info(
                    f"  {f.case_number} | {f.tenant_name} | {f.filing_date} | {f.county} | {f.property_address[:40]}"
                )
        else:
            log.info(f"{label}: no filings found")

    log.info("Georgia scrape job complete")


if __name__ == "__main__":
    asyncio.run(main())
