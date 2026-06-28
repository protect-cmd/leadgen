import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers.florida.miami_dade import MiamiDadeScraper
from scrapers.florida.broward import BrowardScraper
from scrapers.florida.hillsborough import HillsboroughScraper
from scrapers.florida.duval import DuvalScraper
from pipeline import runner
from services import notification_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)

# key -> (label, scraper factory). --counties picks a subset so the scheduler can
# run only the working FL sources (e.g. duval) and skip the blocked Hillsborough
# (WAF/403) or dormant Miami-Dade/Broward.
_FL_COUNTIES = {
    "miami-dade": ("Miami-Dade County", MiamiDadeScraper),
    "broward": ("Broward County", BrowardScraper),
    "hillsborough": ("Hillsborough County", HillsboroughScraper),
    "duval": ("Duval County", DuvalScraper),
}


async def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Florida eviction scrape")
    ap.add_argument("--counties", help="comma-separated subset (default: all), e.g. duval")
    args = ap.parse_args(argv)
    only = {c.strip().lower() for c in args.counties.split(",")} if args.counties else None

    log.info("Starting Florida scrape job (%s)", args.counties or "all counties")

    scrapers = [
        (label, factory(lookback_days=2))
        for key, (label, factory) in _FL_COUNTIES.items()
        if only is None or key in only
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
    asyncio.run(main(sys.argv[1:]))
