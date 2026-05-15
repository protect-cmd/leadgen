from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.filing import Filing
from scrapers.ohio.franklin import FranklinCountyMunicipalScraper
from scrapers.ohio.hamilton import HamiltonCountyMunicipalScraper
from services import notification_service

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OhioRunSummary:
    franklin_filings: int
    hamilton_filings: int
    piped: bool

    @property
    def total_filings(self) -> int:
        return self.franklin_filings + self.hamilton_filings

    def to_lines(self) -> list[str]:
        runner_line = (
            f"Runner: called with {self.total_filings} filings"
            if self.piped
            else "Runner/enrichment/outreach: not called (scraper-only mode)"
        )
        return [
            "Ohio" + (" pipeline run" if self.piped else " scraper-only proof"),
            f"Franklin Municipal (Columbus): {self.franklin_filings} filings",
            f"Hamilton Municipal (Cincinnati): {self.hamilton_filings} filings",
            f"Total: {self.total_filings}",
            runner_line,
        ]


async def main(
    *,
    lookback_days: int = 2,
    pipe: bool = False,
    notify: bool = False,
    counties: list[str] | None = None,
) -> OhioRunSummary:
    run_franklin = not counties or "franklin" in counties
    run_hamilton = not counties or "hamilton" in counties

    log.info("Starting Ohio %s", "pipeline run" if pipe else "scraper-only proof")

    franklin_filings: list[Filing] = []
    if run_franklin:
        scraper = FranklinCountyMunicipalScraper(lookback_days=lookback_days)
        try:
            franklin_filings = scraper.scrape()
        except Exception as e:
            log.error("Ohio / Franklin: unexpected error: %s", e, exc_info=True)

    hamilton_filings: list[Filing] = []
    if run_hamilton:
        scraper = HamiltonCountyMunicipalScraper(lookback_days=lookback_days)
        try:
            hamilton_filings = scraper.scrape()
        except Exception as e:
            log.error("Ohio / Hamilton: unexpected error: %s", e, exc_info=True)

    if pipe and franklin_filings:
        from pipeline import runner as pipeline_runner
        await pipeline_runner.run(franklin_filings, state="OH", county="Franklin")

    if pipe and hamilton_filings:
        from pipeline import runner as pipeline_runner
        await pipeline_runner.run(hamilton_filings, state="OH", county="Hamilton")

    summary = OhioRunSummary(
        franklin_filings=len(franklin_filings),
        hamilton_filings=len(hamilton_filings),
        piped=pipe,
    )

    message = "\n".join(summary.to_lines())
    print(message)

    if notify:
        await notification_service.send_alert(
            "Ohio run",
            message,
            tags={"mode": "pipeline" if pipe else "scraper-only"},
        )

    log.info("Ohio run complete")
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run Ohio eviction scrapers (Franklin + Hamilton). "
            "Default: scraper-only proof. "
            "Use --pipe to send filings through the BatchData enrichment pipeline."
        )
    )
    parser.add_argument("--lookback-days", type=int, default=2)
    parser.add_argument(
        "--counties",
        default="",
        help="Comma-separated counties to run: franklin, hamilton. Default: all.",
    )
    parser.add_argument("--pipe", action="store_true")
    parser.add_argument("--notify", action="store_true")
    return parser


def cli() -> int:
    args = _build_parser().parse_args()
    counties = [c.strip().lower() for c in args.counties.split(",") if c.strip()] or None
    asyncio.run(
        main(
            lookback_days=args.lookback_days,
            pipe=args.pipe,
            notify=args.notify,
            counties=counties,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
