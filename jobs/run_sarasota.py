from __future__ import annotations

"""
Sarasota County FL eviction scraper runner.

Usage:
    python -m jobs.run_sarasota                       # scraper-only, no DB write
    python -m jobs.run_sarasota --yes-write-supabase
    python -m jobs.run_sarasota --lookback-days 7
    python -m jobs.run_sarasota --dry-run
"""

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.filing import Filing
from scrapers.florida.sarasota import SarasotaScraper

logging.basicConfig(
    level="INFO",
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SarasotaRunSummary:
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
            "Sarasota County FL — Eviction",
            f"Mode:                  {mode}",
            f"Lookback days:         {self.lookback_days}",
            f"Total eviction filings:{self.total_filings}",
        ]


async def main(
    *,
    lookback_days: int = 2,
    dry_run: bool = False,
    yes_write_supabase: bool = False,
    headless: bool = True,
) -> SarasotaRunSummary:

    log.info("Sarasota FL: starting run (lookback=%d days)", lookback_days)

    scraper = SarasotaScraper(lookback_days=lookback_days, headless=headless)
    filings: list[Filing] = await scraper.scrape()

    log.info("Sarasota FL: %d eviction filings", len(filings))

    if dry_run:
        log.info("Sarasota FL: dry-run — printing first 25 filings")
        for f in filings[:25]:
            log.info(
                "  %s | %s | landlord=%s | filed=%s",
                f.case_number, f.tenant_name, f.landlord_name, f.filing_date,
            )
        log.info("Sarasota FL: dry-run — %d filings NOT written", len(filings))
        return SarasotaRunSummary(
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
            "Sarasota FL: Supabase push — %d inserted, %d duplicates",
            inserted, duplicates,
        )
        wrote = True

    summary = SarasotaRunSummary(
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
            "Run Sarasota County FL eviction scraper. "
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
    p.add_argument("--no-headless", action="store_true",
                   help="Run browser in visible (non-headless) mode")
    return p


def cli() -> int:
    args = _build_parser().parse_args()
    asyncio.run(
        main(
            lookback_days=args.lookback_days,
            dry_run=args.dry_run,
            yes_write_supabase=args.yes_write_supabase,
            headless=not args.no_headless,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
