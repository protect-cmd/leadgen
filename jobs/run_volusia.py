from __future__ import annotations

"""
Volusia County FL eviction scraper runner.

Usage:
    python -m jobs.run_volusia                       # scraper-only, no DB write
    python -m jobs.run_volusia --yes-write-supabase
    python -m jobs.run_volusia --lookback-days 7
    python -m jobs.run_volusia --dry-run
"""

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.filing import Filing
from scrapers.florida.volusia import VolusiaScraper

logging.basicConfig(
    level="INFO",
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VolusiaRunSummary:
    total_filings: int
    lookback_days: int
    dry_run: bool
    wrote_supabase: bool

    def to_lines(self) -> list[str]:
        mode = (
            "dry-run" if self.dry_run
            else ("supabase-write" if self.wrote_supabase else "scraper-only")
        )
        return [
            "Volusia County FL — Eviction",
            f"Mode:                  {mode}",
            f"Lookback days:         {self.lookback_days}",
            f"Total eviction filings:{self.total_filings}",
        ]


async def main(
    *,
    lookback_days: int = 2,
    dry_run: bool = False,
    yes_write_supabase: bool = False,
) -> VolusiaRunSummary:

    log.info("Volusia FL: starting run (lookback=%d days)", lookback_days)

    scraper = VolusiaScraper(lookback_days=lookback_days)
    filings: list[Filing] = await scraper.scrape()

    log.info("Volusia FL: %d eviction filings", len(filings))

    if dry_run:
        log.info("Volusia FL: dry-run — printing first 25 filings")
        for f in filings[:25]:
            log.info(
                "  %s | %s | landlord=%s | filed=%s",
                f.case_number, f.tenant_name, f.landlord_name, f.filing_date,
            )
        log.info("Volusia FL: dry-run — %d filings NOT written", len(filings))
        return VolusiaRunSummary(
            total_filings=len(filings),
            lookback_days=lookback_days,
            dry_run=True,
            wrote_supabase=False,
        )

    wrote = False
    if yes_write_supabase and filings:
        from services import dedup_service
        inserted = duplicates = 0
        for filing in filings:
            if await dedup_service.is_duplicate(filing.case_number):
                duplicates += 1
            else:
                await dedup_service.insert_filing(filing)
                inserted += 1
        log.info(
            "Volusia FL: Supabase push — %d inserted, %d duplicates",
            inserted, duplicates,
        )
        wrote = True

    summary = VolusiaRunSummary(
        total_filings=len(filings),
        lookback_days=lookback_days,
        dry_run=False,
        wrote_supabase=wrote,
    )
    print("\n".join(summary.to_lines()))
    return summary


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Run Volusia County FL eviction scraper. "
            "Default: scraper-only (no DB write). "
            "Add --yes-write-supabase to push eviction filings to Supabase."
        )
    )
    p.add_argument("--lookback-days", type=int, default=2,
                   help="Number of days back to search (default: 2)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print filings without writing to Supabase")
    p.add_argument("--yes-write-supabase", action="store_true",
                   help="Insert eviction filings into Supabase")
    return p


def cli() -> int:
    args = _build_parser().parse_args()
    asyncio.run(
        main(
            lookback_days=args.lookback_days,
            dry_run=args.dry_run,
            yes_write_supabase=args.yes_write_supabase,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
