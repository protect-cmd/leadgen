from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from models.filing import Filing
from scrapers.ohio.barberton import BarbertonMunicipalScraper
from scrapers.ohio.butler import ButlerCountyAreaCourtScraper
from scrapers.ohio.franklin import FranklinCountyMunicipalScraper
from scrapers.ohio.hamilton import HamiltonCountyMunicipalScraper
from scrapers.ohio.lorain import ElyriaMunicipalScraper
from scrapers.ohio.montgomery import MontgomeryCountyMunicipalScraper
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
    montgomery_filings: int
    barberton_filings: int
    butler_filings: int
    lorain_filings: int
    piped: bool
    wrote_supabase: bool = False

    @property
    def total_filings(self) -> int:
        return (
            self.franklin_filings
            + self.hamilton_filings
            + self.montgomery_filings
            + self.barberton_filings
            + self.butler_filings
            + self.lorain_filings
        )

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
            f"Montgomery Municipal (Dayton): {self.montgomery_filings} filings",
            f"Barberton Municipal (Summit): {self.barberton_filings} filings",
            f"Butler Area Courts (Oxford/Hamilton/West Chester): {self.butler_filings} filings",
            f"Elyria Municipal (Lorain): {self.lorain_filings} filings",
            f"Total: {self.total_filings}",
            runner_line,
        ]


async def main(
    *,
    lookback_days: int = 2,
    pipe: bool = False,
    notify: bool = False,
    yes_write_supabase: bool = False,
    counties: list[str] | None = None,
) -> OhioRunSummary:
    run_franklin = not counties or "franklin" in counties
    run_hamilton = not counties or "hamilton" in counties
    run_montgomery = not counties or "montgomery" in counties
    run_barberton = not counties or "barberton" in counties or "summit" in counties
    run_butler = not counties or "butler" in counties
    run_lorain = not counties or "lorain" in counties

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

    montgomery_filings: list[Filing] = []
    if run_montgomery:
        scraper = MontgomeryCountyMunicipalScraper(lookback_days=lookback_days)
        try:
            montgomery_filings = scraper.scrape()
        except Exception as e:
            log.error("Ohio / Montgomery: unexpected error: %s", e, exc_info=True)

    barberton_filings: list[Filing] = []
    if run_barberton:
        scraper = BarbertonMunicipalScraper(lookback_days=lookback_days)
        try:
            barberton_filings = scraper.scrape()
        except Exception as e:
            log.error("Ohio / Barberton: unexpected error: %s", e, exc_info=True)

    butler_filings: list[Filing] = []
    if run_butler:
        scraper = ButlerCountyAreaCourtScraper(lookback_days=lookback_days)
        try:
            butler_filings = scraper.scrape()
        except Exception as e:
            log.error("Ohio / Butler: unexpected error: %s", e, exc_info=True)

    lorain_filings: list[Filing] = []
    if run_lorain:
        scraper = ElyriaMunicipalScraper(lookback_days=lookback_days)
        try:
            lorain_filings = scraper.scrape()
        except Exception as e:
            log.error("Ohio / Lorain: unexpected error: %s", e, exc_info=True)

    if yes_write_supabase:
        from services import dedup_service
        all_filings = (
            franklin_filings
            + hamilton_filings
            + montgomery_filings
            + barberton_filings
            + butler_filings
            + lorain_filings
        )
        inserted = duplicates = 0
        for filing in all_filings:
            if await dedup_service.is_duplicate(filing.case_number):
                duplicates += 1
                await dedup_service.backfill_address(
                    filing.case_number, filing.property_address
                )
            else:
                await dedup_service.insert_filing(filing)
                inserted += 1
        log.info("Ohio Supabase push: %d inserted, %d duplicates", inserted, duplicates)

    if pipe and franklin_filings:
        from pipeline import runner as pipeline_runner
        await pipeline_runner.run(franklin_filings, state="OH", county="Franklin")

    if pipe and hamilton_filings:
        from pipeline import runner as pipeline_runner
        await pipeline_runner.run(hamilton_filings, state="OH", county="Hamilton")

    if pipe and montgomery_filings:
        from pipeline import runner as pipeline_runner
        await pipeline_runner.run(montgomery_filings, state="OH", county="Montgomery")

    if pipe and barberton_filings:
        from pipeline import runner as pipeline_runner
        await pipeline_runner.run(barberton_filings, state="OH", county="Summit")

    if pipe and butler_filings:
        from pipeline import runner as pipeline_runner
        await pipeline_runner.run(butler_filings, state="OH", county="Butler")

    if pipe and lorain_filings:
        from pipeline import runner as pipeline_runner
        await pipeline_runner.run(lorain_filings, state="OH", county="Lorain")

    summary = OhioRunSummary(
        franklin_filings=len(franklin_filings),
        hamilton_filings=len(hamilton_filings),
        montgomery_filings=len(montgomery_filings),
        barberton_filings=len(barberton_filings),
        butler_filings=len(butler_filings),
        lorain_filings=len(lorain_filings),
        piped=pipe,
        wrote_supabase=yes_write_supabase,
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
            "Run Ohio eviction scrapers "
            "(Franklin + Hamilton + Montgomery + Barberton + Butler + Lorain). "
            "Default: scraper-only proof. "
            "Use --yes-write-supabase to insert filings into Supabase (no pipeline). "
            "Use --pipe to send filings through the BatchData enrichment pipeline."
        )
    )
    parser.add_argument("--lookback-days", type=int, default=2)
    parser.add_argument(
        "--counties",
        default="",
        help=(
            "Comma-separated counties to run: "
            "franklin, hamilton, montgomery, barberton/summit, butler, lorain. "
            "Default: all."
        ),
    )
    parser.add_argument(
        "--yes-write-supabase",
        action="store_true",
        help="Insert filings into Supabase. Does not call enrichment or outreach.",
    )
    parser.add_argument("--pipe", action="store_true")
    parser.add_argument("--notify", action="store_true")
    return parser


def cli() -> int:
    args = _build_parser().parse_args()
    load_dotenv()
    counties = [c.strip().lower() for c in args.counties.split(",") if c.strip()] or None
    asyncio.run(
        main(
            lookback_days=args.lookback_days,
            pipe=args.pipe,
            notify=args.notify,
            yes_write_supabase=args.yes_write_supabase,
            counties=counties,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
