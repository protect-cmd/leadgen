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
from scrapers.georgia.researchga import (
    DEFAULT_HEARING_LOOKAHEAD_DAYS,
    ReSearchGAScraper,
)
from services import notification_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class GeorgiaRunSummary:
    total_filings: int
    lookback_days: int
    hearing_lookahead_days: int
    piped: bool

    def to_lines(self) -> list[str]:
        runner_line = (
            f"Runner: called with {self.total_filings} filings"
            if self.piped
            else "Runner/enrichment/outreach: not called (scraper-only mode)"
        )
        return [
            "Georgia / re:SearchGA" + (" pipeline run" if self.piped else " scraper-only proof"),
            f"Lookback days: {self.lookback_days}",
            f"Hearing lookahead days: {self.hearing_lookahead_days}",
            f"Total filings: {self.total_filings}",
            "Address source: not exposed in re:SearchGA hearing data",
            "Tenant enrichment: pending Melissa Personator integration",
            runner_line,
        ]


def build_summary(
    *,
    filings: list[Filing],
    lookback_days: int,
    hearing_lookahead_days: int,
    piped: bool,
) -> GeorgiaRunSummary:
    return GeorgiaRunSummary(
        total_filings=len(filings),
        lookback_days=lookback_days,
        hearing_lookahead_days=hearing_lookahead_days,
        piped=piped,
    )


async def main(
    *,
    lookback_days: int = 2,
    hearing_lookahead_days: int = DEFAULT_HEARING_LOOKAHEAD_DAYS,
    pipe: bool = False,
) -> GeorgiaRunSummary:
    log.info("Starting Georgia / re:SearchGA %s", "pipeline run" if pipe else "scraper-only proof")

    scraper = ReSearchGAScraper(
        lookback_days=lookback_days,
        hearing_lookahead_days=hearing_lookahead_days,
    )

    try:
        filings = await scraper.scrape()
    except Exception as e:
        log.error("Georgia / re:SearchGA: unexpected error: %s", e, exc_info=True)
        await notification_service.send_job_error(
            job="Georgia / re:SearchGA",
            stage="scrape",
            error=str(e),
        )
        filings = []

    if pipe:
        if filings:
            await runner.run(filings, state="GA", county="re:SearchGA")
        else:
            log.info("re:SearchGA: no filings to pipe")

    summary = build_summary(
        filings=filings,
        lookback_days=lookback_days,
        hearing_lookahead_days=hearing_lookahead_days,
        piped=pipe,
    )
    print("\n".join(summary.to_lines()))

    log.info("Georgia / re:SearchGA run complete")
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run Georgia / re:SearchGA dispossessory hearing scraper. "
            "Default: scraper-only proof. Add --pipe only after Melissa "
            "Personator enrichment is wired and approved."
        )
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=2,
        help="Days before today to include in the hearing-date search window (default 2)",
    )
    parser.add_argument(
        "--hearing-lookahead-days",
        type=int,
        default=DEFAULT_HEARING_LOOKAHEAD_DAYS,
        help=(
            "Days after today to include in the hearing-date search window "
            f"(default {DEFAULT_HEARING_LOOKAHEAD_DAYS})"
        ),
    )
    parser.add_argument("--pipe", action="store_true")
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    asyncio.run(
        main(
            lookback_days=args.lookback_days,
            hearing_lookahead_days=args.hearing_lookahead_days,
            pipe=args.pipe,
        )
    )
