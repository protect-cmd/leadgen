from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.filing import Filing
from pipeline import runner
from scrapers.texas.tarrant import TarrantCountyJPScraper
from services import notification_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TarrantRunSummary:
    total_filings: int
    with_address: int
    lookback_days: int
    piped: bool

    def to_lines(self) -> list[str]:
        runner_line = (
            f"Runner: called with {self.total_filings} filings"
            if self.piped
            else "Runner/enrichment/outreach: not called (scraper-only mode)"
        )
        return [
            "Tarrant County TX / JP Courts"
            + (" pipeline run" if self.piped else " scraper-only proof"),
            f"Lookback days: {self.lookback_days}",
            f"Total eviction filings: {self.total_filings}",
            f"Filings with address: {self.with_address} "
            f"({self.with_address * 100 // self.total_filings if self.total_filings else 0}%)",
            "Address source: defendant address from Odyssey CaseDetail (rental unit)",
            "Proxy: Bright Data Scraping Browser (BRIGHTDATA_SB_WS)",
            runner_line,
        ]


def build_summary(
    *,
    filings: list[Filing],
    lookback_days: int,
    piped: bool,
) -> TarrantRunSummary:
    with_address = sum(1 for f in filings if f.property_address != "Unknown")
    return TarrantRunSummary(
        total_filings=len(filings),
        with_address=with_address,
        lookback_days=lookback_days,
        piped=piped,
    )


async def main(
    *,
    lookback_days: int = 2,
    pipe: bool = False,
) -> TarrantRunSummary:
    log.info(
        "Starting Tarrant County TX %s",
        "pipeline run" if pipe else "scraper-only proof",
    )

    scraper = TarrantCountyJPScraper(lookback_days=lookback_days)

    try:
        filings = await scraper.scrape()
    except Exception as e:
        log.error("Tarrant TX: unexpected error: %s", e, exc_info=True)
        await notification_service.send_job_error(
            job="Tarrant TX JP Courts",
            stage="scrape",
            error=str(e),
        )
        filings = []

    if pipe:
        if filings:
            await runner.run(filings, state="TX", county="Tarrant")
        else:
            log.info("Tarrant TX: no filings to pipe")

    summary = build_summary(filings=filings, lookback_days=lookback_days, piped=pipe)
    print("\n".join(summary.to_lines()))

    log.info("Tarrant TX run complete")
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run Tarrant County TX JP Courts eviction scraper via Bright Data. "
            "Requires BRIGHTDATA_SB_WS env var (or BRIGHTDATA_CUSTOMER_ID / "
            "BRIGHTDATA_ZONE / BRIGHTDATA_ZONE_PASSWORD). "
            "Default: scraper-only proof. Add --pipe once ready for production."
        )
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=2,
        help="Days before today to include in the filing-date search window (default 2)",
    )
    parser.add_argument("--pipe", action="store_true")
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    asyncio.run(main(lookback_days=args.lookback_days, pipe=args.pipe))
