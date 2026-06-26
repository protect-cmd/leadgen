"""Railway cron entry point for Cosner Drake (Indiana MyCase debt suits).

Phase 1 raw ingest: scrape statewide CC (Civil Collection) filings and
insert/dedupe into cd_debt_suits. Does NOT call enrichment, GHL, Bland, or the
eviction pipeline runner — that's a later phase.

Usage:
    python jobs/run_indiana_debt.py --lookback-days 2            # scrape + write
    python jobs/run_indiana_debt.py --lookback-days 14 --dry-run # scrape only
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Cosner Drake Indiana MyCase debt raw ingest.")
    p.add_argument("--lookback-days", type=int, default=2)
    p.add_argument("--max-cases", type=int, default=None,
                   help="Cap detail fetches (for quick smokes).")
    p.add_argument("--include-small-claims", action="store_true",
                   help="Also collect SC (Small Claims) in addition to CC.")
    p.add_argument("--dry-run", action="store_true",
                   help="Scrape and report only; do not write to Supabase.")
    return p


async def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    from scrapers.indiana.mycase_debt import IndianaMyCaseDebtScraper

    case_types = ("CC", "SC") if args.include_small_claims else ("CC",)
    log.info("Starting Cosner Drake Indiana debt ingest "
             f"(lookback={args.lookback_days}d, types={case_types}, dry_run={args.dry_run})")

    scraper = IndianaMyCaseDebtScraper(
        lookback_days=args.lookback_days,
        case_types=case_types,
        max_cases=args.max_cases,
    )
    suits = await scraper.scrape()
    log.info(f"Scraped {len(suits)} debt suits with usable addresses. stats={scraper.stats}")

    if scraper.last_error:
        log.warning(f"Scraper reported last_error={scraper.last_error!r}")

    if not suits:
        log.info("No suits to ingest.")
        return

    if args.dry_run:
        log.info("Dry run — not writing to Supabase.")
        for s in suits[:10]:
            log.info(f"  [{s.county}] {s.defendant_name} | {s.defendant_address} "
                     f"| creditor={s.plaintiff_name} | {s.case_number}")
        return

    from services import cd_debt_store

    inserted = await cd_debt_store.insert_suits(suits)
    log.info(f"Cosner Drake ingest complete: {inserted} new / {len(suits)} scraped "
             f"({len(suits) - inserted} duplicates skipped)")


if __name__ == "__main__":
    asyncio.run(main())
