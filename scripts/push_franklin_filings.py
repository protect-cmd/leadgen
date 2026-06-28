from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Awaitable, Callable

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from models.filing import Filing
from scrapers.ohio.franklin import FranklinCountyMunicipalScraper


IsDuplicateFunc = Callable[[str], Awaitable[bool]]
InsertFilingFunc = Callable[[Filing], Awaitable[None]]


@dataclass(frozen=True)
class PushSummary:
    received: int
    inserted: int
    duplicates: int


async def push_filings_to_supabase(
    filings: list[Filing],
    *,
    is_duplicate: IsDuplicateFunc,
    insert_filing: InsertFilingFunc,
) -> PushSummary:
    inserted = 0
    duplicates = 0
    for filing in filings:
        if await is_duplicate(filing.case_number):
            duplicates += 1
            continue
        await insert_filing(filing)
        inserted += 1
    return PushSummary(
        received=len(filings),
        inserted=inserted,
        duplicates=duplicates,
    )


async def _emit_run_metrics(summary: PushSummary) -> None:
    """Write a run_metrics row so the raw-push job is visible to monitoring
    (it bypasses the runner, which is why Franklin showed 0 metrics rows)."""
    from datetime import datetime, timezone
    from services import dedup_service
    await dedup_service.write_run_metrics({
        "run_at": datetime.now(timezone.utc).isoformat(),
        "state": "OH",
        "county": "Franklin",
        "filings_received": summary.received,
        "duplicates_skipped": summary.duplicates,
    })


def format_summary(summary: PushSummary) -> list[str]:
    return [
        "Franklin Supabase filing push",
        f"Filings scraped: {summary.received}",
        f"Inserted: {summary.inserted}",
        f"Duplicates skipped: {summary.duplicates}",
        "Downstream outreach/enrichment: not called",
    ]


async def main_async(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Push raw Franklin County OH filing rows to Supabase. "
            "Does not call BatchData, GHL, Bland, Instantly, or pipeline runner."
        )
    )
    parser.add_argument("--lookback-days", type=int, default=14)
    parser.add_argument("--max-cases", type=int, default=0, help="0 means no cap")
    parser.add_argument(
        "--yes-write-supabase",
        action="store_true",
        help="Required because this inserts rows into Supabase.",
    )
    parser.add_argument("--notify", action="store_true", help="Send Pushover summary if enabled.")
    args = parser.parse_args(argv)

    if not args.yes_write_supabase:
        parser.error("--yes-write-supabase is required because this writes to Supabase")

    load_dotenv()

    scraper = FranklinCountyMunicipalScraper(lookback_days=args.lookback_days)
    filings = scraper.scrape()
    if args.max_cases > 0:
        filings = filings[: args.max_cases]

    from services import dedup_service

    summary = await push_filings_to_supabase(
        filings,
        is_duplicate=dedup_service.is_duplicate,
        insert_filing=dedup_service.insert_filing,
    )
    for line in format_summary(summary):
        print(line)
    await _emit_run_metrics(summary)
    if args.notify:
        from services import notification_service

        await notification_service.send_scrape_summary(
            source="Franklin OH",
            scraped=summary.received,
            inserted=summary.inserted,
            duplicates=summary.duplicates,
            piped=False,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
