import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers.georgia.researchga import ReSearchGAScraper
from pipeline import runner
from services import notification_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


async def main(*, lookback_days: int = 2) -> None:
    log.info("Starting Georgia scrape job")

    scrapers = [
        ("re:SearchGA", ReSearchGAScraper(lookback_days=lookback_days)),
    ]

    for label, scraper in scrapers:
        log.info(f"=== Georgia / {label} ===")
        try:
            filings = await scraper.scrape()
        except Exception as e:
            log.error(f"Georgia / {label}: unexpected error: {e}", exc_info=True)
            await notification_service.send_job_error(
                job=f"Georgia / {label}",
                stage="scrape",
                error=str(e),
            )
            continue

        if filings:
            log.info(f"{label}: {len(filings)} filings scraped")
            await runner.run(filings, state="GA", county=label)
        else:
            log.info(f"{label}: no filings found")

    log.info("Georgia scrape job complete")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Georgia / re:SearchGA scrape job."
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=2,
        help="Days of filing history to fetch (default 2; use 30 for diagnostics)",
    )
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    asyncio.run(main(lookback_days=args.lookback_days))
