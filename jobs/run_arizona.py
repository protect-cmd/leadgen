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
from scrapers.arizona.maricopa import MaricopaJusticeCourtScraper
from services import notification_service

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArizonaRunSummary:
    total_filings: int
    usable_single_match: int
    ambiguous: int
    no_match: int
    errors: int
    max_cases: int
    lookback_days: int
    piped: bool

    @property
    def held_for_review(self) -> int:
        return self.ambiguous + self.no_match + self.errors

    def to_lines(self) -> list[str]:
        runner_line = (
            f"Runner: called with {self.usable_single_match} single-match filings"
            if self.piped
            else "Runner/enrichment/outreach: not called (scraper-only mode)"
        )
        return [
            "Arizona / Maricopa" + (" pipeline run" if self.piped else " scraper-only proof"),
            f"Max cases: {self.max_cases}",
            f"Lookback days: {self.lookback_days}",
            f"Total filings: {self.total_filings}",
            f"Usable single-match addresses: {self.usable_single_match}",
            f"Held for review: {self.held_for_review}",
            f"Ambiguous owner matches: {self.ambiguous}",
            f"No owner match: {self.no_match}",
            f"Match errors: {self.errors}",
            runner_line,
        ]


def build_summary(
    *,
    filings: list[Filing],
    address_match_counts: dict[str, int],
    max_cases: int,
    lookback_days: int,
    piped: bool,
) -> ArizonaRunSummary:
    return ArizonaRunSummary(
        total_filings=len(filings),
        usable_single_match=int(address_match_counts.get("single_match", 0)),
        ambiguous=int(address_match_counts.get("ambiguous", 0)),
        no_match=int(address_match_counts.get("no_match", 0)),
        errors=int(address_match_counts.get("error", 0)),
        max_cases=max_cases,
        lookback_days=lookback_days,
        piped=piped,
    )


async def main(
    *,
    max_cases: int = 50,
    lookback_days: int = 2,
    notify: bool = False,
    pipe: bool = False,
    yes_write_supabase: bool = False,
) -> ArizonaRunSummary:
    log.info("Starting Arizona / Maricopa %s", "pipeline run" if pipe else "scraper-only proof")
    scraper = MaricopaJusticeCourtScraper(
        lookback_days=lookback_days,
        max_cases=max_cases,
        enrich_addresses=True,
    )
    filings = scraper.scrape()

    # Only single-match assessor addresses are eligible downstream (AGENTS.md:
    # "Only single_match rows should be eligible"). Compute once for both the
    # raw-insert and the (enriching) pipe paths.
    single_match_filings = [
        f for f in filings
        if scraper.address_matches_by_case.get(f.case_number) is not None
        and scraper.address_matches_by_case[f.case_number].status == "single_match"
        and f.property_address not in ("Unknown", "", None)
    ]

    if yes_write_supabase:
        # Raw insert (no enrichment/outreach), matching the OH jobs. run_arizona
        # previously persisted ONLY via --pipe, so the scheduled job (--notify
        # only) discarded every scrape — Maricopa got near-zero daily volume.
        from services import dedup_service
        inserted = duplicates = 0
        for filing in single_match_filings:
            if await dedup_service.is_duplicate(filing.case_number):
                duplicates += 1
                await dedup_service.backfill_address(
                    filing.case_number, filing.property_address
                )
            else:
                await dedup_service.insert_filing(filing)
                inserted += 1
        log.info(
            "Arizona Supabase push (single-match raw): %d inserted, %d duplicates",
            inserted, duplicates,
        )

    if pipe:
        from pipeline import runner as pipeline_runner

        log.info(
            "Arizona: passing %d single-match filings to pipeline (%d held)",
            len(single_match_filings),
            len(filings) - len(single_match_filings),
        )
        if single_match_filings:
            await pipeline_runner.run(single_match_filings, state="AZ", county="Maricopa")
        else:
            log.info("Arizona: no single-match filings to pipe")

    summary = build_summary(
        filings=filings,
        address_match_counts=scraper.address_match_counts,
        max_cases=max_cases,
        lookback_days=lookback_days,
        piped=pipe,
    )

    message = "\n".join(summary.to_lines())
    print(message)

    if notify:
        await notification_service.send_alert(
            "Arizona Maricopa run",
            message,
            tags={"mode": "pipeline" if pipe else "scraper-only"},
        )

    log.info("Arizona / Maricopa run complete")
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run Arizona / Maricopa eviction calendar scraper. "
            "Default: scraper-only proof (no pipeline calls). "
            "Add --pipe to send single-match filings through BatchData / GHL / Bland."
        )
    )
    parser.add_argument("--max-cases", type=int, default=50)
    parser.add_argument("--lookback-days", type=int, default=2)
    parser.add_argument("--notify", action="store_true")
    parser.add_argument(
        "--yes-write-supabase",
        action="store_true",
        help="Raw-insert single-match filings into Supabase (no enrichment/outreach).",
    )
    parser.add_argument(
        "--pipe",
        action="store_true",
        help="Send single-match address filings through the pipeline runner (enriches).",
    )
    return parser


def cli() -> int:
    args = _build_parser().parse_args()
    asyncio.run(
        main(
            max_cases=args.max_cases,
            lookback_days=args.lookback_days,
            notify=args.notify,
            pipe=args.pipe,
            yes_write_supabase=args.yes_write_supabase,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
