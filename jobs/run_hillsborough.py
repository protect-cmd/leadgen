from __future__ import annotations

"""
Hillsborough County FL eviction scraper runner.

Usage:
    python -m jobs.run_hillsborough                    # scraper-only, no DB write
    python -m jobs.run_hillsborough --yes-write-supabase
    python -m jobs.run_hillsborough --lookback-days 14
    python -m jobs.run_hillsborough --dry-run
"""

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.filing import Filing
from scrapers.florida.hillsborough import HillsboroughScraper

logging.basicConfig(
    level="INFO",
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class HillsboroughRunSummary:
    total_filings: int
    defendants_with_address: int
    defendants_unknown_address: int
    lookback_days: int
    dry_run: bool
    wrote_supabase: bool

    def to_lines(self) -> list[str]:
        mode = "dry-run" if self.dry_run else ("supabase-write" if self.wrote_supabase else "scraper-only")
        return [
            "Hillsborough County FL — Residential Eviction",
            f"Mode:                  {mode}",
            f"Lookback days:         {self.lookback_days}",
            f"Total filings:         {self.total_filings}",
            f"With address:          {self.defendants_with_address}",
            f"Unknown address:       {self.defendants_unknown_address}",
        ]


async def main(
    *,
    lookback_days: int = 7,
    dry_run: bool = False,
    yes_write_supabase: bool = False,
    headless: bool = True,
    max_cases: int = 200,
    fetch_addresses: bool = True,
) -> HillsboroughRunSummary:

    log.info(
        "Hillsborough FL: starting run (lookback=%d days, max_cases=%d, addresses=%s)",
        lookback_days, max_cases, fetch_addresses,
    )

    scraper = HillsboroughScraper(
        lookback_days=lookback_days,
        headless=headless,
        max_cases=max_cases,
        fetch_addresses=fetch_addresses,
    )
    filings: list[Filing] = await scraper.scrape()
    if scraper.last_error:
        log.warning("Hillsborough FL: scraper reported last_error: %s", scraper.last_error)

    with_addr    = [f for f in filings if f.property_address not in ("Unknown", "", None)]
    without_addr = [f for f in filings if f.property_address in ("Unknown", "", None)]

    log.info(
        "Hillsborough FL: %d total | %d with address | %d unknown address",
        len(filings), len(with_addr), len(without_addr),
    )

    if dry_run:
        log.info("Hillsborough FL: dry-run — printing first 25 filings")
        for f in filings[:25]:
            log.info(
                "  %s | %s | %s | filed=%s",
                f.case_number, f.tenant_name, f.property_address, f.filing_date,
            )
        log.info("Hillsborough FL: dry-run — %d filings NOT written", len(filings))
        return HillsboroughRunSummary(
            total_filings=len(filings),
            defendants_with_address=len(with_addr),
            defendants_unknown_address=len(without_addr),
            lookback_days=lookback_days,
            dry_run=True,
            wrote_supabase=False,
        )

    wrote = False
    if yes_write_supabase and with_addr:
        from services import dedup_service
        inserted = duplicates = 0
        for filing in with_addr:
            if await dedup_service.is_duplicate(filing.case_number):
                duplicates += 1
                await dedup_service.backfill_address(
                    filing.case_number, filing.property_address
                )
            else:
                await dedup_service.insert_filing(filing)
                inserted += 1
        log.info(
            "Hillsborough FL: Supabase push — %d inserted, %d duplicates",
            inserted, duplicates,
        )
        wrote = True

    summary = HillsboroughRunSummary(
        total_filings=len(filings),
        defendants_with_address=len(with_addr),
        defendants_unknown_address=len(without_addr),
        lookback_days=lookback_days,
        dry_run=False,
        wrote_supabase=wrote,
    )
    print("\n".join(summary.to_lines()))
    return summary


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Run Hillsborough County FL eviction scraper. "
            "Default: scraper-only (no DB write). "
            "Add --yes-write-supabase to push filings with addresses to Supabase."
        )
    )
    p.add_argument("--lookback-days", type=int, default=7,
                   help="Number of days back to search (default: 7)")
    p.add_argument("--max-cases", type=int, default=200,
                   help="Max cases to open for address lookup (default: 200)")
    p.add_argument("--no-addresses", action="store_true",
                   help="Skip per-case detail visits; grid data only (faster, no addresses)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print filings without writing to Supabase")
    p.add_argument("--yes-write-supabase", action="store_true",
                   help="Insert filings with addresses into Supabase")
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
            max_cases=args.max_cases,
            fetch_addresses=not args.no_addresses,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())