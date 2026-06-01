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
from scrapers.ohio.montgomery import MontgomeryCountyMunicipalScraper
from services import notification_service

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MontgomeryRunSummary:
    montgomery_filings: int
    inserted: int
    duplicates: int
    piped: bool
    wrote_supabase: bool

    @property
    def total_filings(self) -> int:
        return self.montgomery_filings

    def to_lines(self) -> list[str]:
        lines = [
            "Montgomery" + (" pipeline run" if self.piped else " scraper-only proof"),
            f"Montgomery Municipal (Dayton): {self.montgomery_filings} filings",
            f"Total: {self.total_filings}",
        ]
        if self.wrote_supabase:
            lines += [
                f"Supabase inserted: {self.inserted}",
                f"Supabase duplicates skipped: {self.duplicates}",
                "Downstream outreach/enrichment: not called",
            ]
        else:
            runner_line = (
                f"Runner: called with {self.total_filings} filings"
                if self.piped
                else "Runner/enrichment/outreach: not called (scraper-only mode)"
            )
            lines.append(runner_line)
        return lines


async def main(
    *,
    lookback_days: int = 2,
    pipe: bool = False,
    notify: bool = False,
    yes_write_supabase: bool = False,
) -> MontgomeryRunSummary:
    mode = "pipeline run" if pipe else ("supabase push" if yes_write_supabase else "scraper-only proof")
    log.info("Starting Montgomery %s", mode)

    montgomery_filings: list[Filing] = []
    scraper = MontgomeryCountyMunicipalScraper(lookback_days=lookback_days)
    try:
        montgomery_filings = scraper.scrape()
    except Exception as e:
        log.error("Ohio / Montgomery: unexpected error: %s", e, exc_info=True)

    inserted = 0
    duplicates = 0

    if yes_write_supabase and montgomery_filings:
        from services import dedup_service
        for filing in montgomery_filings:
            if await dedup_service.is_duplicate(filing.case_number):
                duplicates += 1
            else:
                await dedup_service.insert_filing(filing)
                inserted += 1
        log.info("Montgomery Supabase push: %d inserted, %d duplicates", inserted, duplicates)

    if pipe and montgomery_filings:
        from pipeline import runner as pipeline_runner
        await pipeline_runner.run(montgomery_filings, state="OH", county="Montgomery")

    summary = MontgomeryRunSummary(
        montgomery_filings=len(montgomery_filings),
        inserted=inserted,
        duplicates=duplicates,
        piped=pipe,
        wrote_supabase=yes_write_supabase,
    )

    message = "\n".join(summary.to_lin