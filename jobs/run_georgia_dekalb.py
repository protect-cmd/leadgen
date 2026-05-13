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
from scrapers.georgia.dekalb import DeKalbDispossessoryScraper
from services import notification_service

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeKalbRunSummary:
    total_filings: int
    max_cases: int
    lookback_days: int
    piped: bool

    def to_lines(self) -> list[str]:
        runner_line = (
            f"Runner: called with {self.total_filings} filings"
            if self.piped
            else "Runner/enrichment/outreach: not called (scraper-only mode)"
        )
        return [
            "Georgia / DeKalb" + (" pipeline run" if self.piped else " scraper-only proof"),
            f"Max cases: {self.max_cases}",
            f"Lookback days: {self.lookback_days}",
            f"Total filings: {self.total_filings}",
            "Address source: not exposed in calendar PDFs",
            "Tenant enrichment: pending Melissa Personator integration",
            runner_line,
        ]


def build_summary(
    *,
    filings: list[Filing],
    max_cases: int,
    lookback_days: int,
    piped: bool,
) -> DeKalbRunSummary:
    return DeKalbRunSummary(
        total_filings=len(filings),
        max_cases=max_cases,
        lookback_days=lookback_days,
        piped=piped,
    )


async def main(
    *,
    max_cases: int = 200,
    lookback_days: int = 2,
    notify: bool = False,
    pipe: bool = False,
) -> DeKalbRunSummary:
    log.info("Starting Georgia / DeKalb %s", "pipeline run" if pipe else "scraper-only proof")
    scraper = DeKalbDispossessoryScraper(lookback_days=lookback_days, max_cases=max_cases)
    filings = scraper.scrape()

    if pipe:
        from pipeline import runner as pipeline_runner

        if filings:
            await pipeline_runner.run(filings, state="GA", county="DeKalb")
        else:
            log.info("DeKalb GA: no filings to pipe")

    summary = build_summary(
        filings=filings,
        max_cases=max_cases,
        lookback_days=lookback_days,
        piped=pipe,
    )
    message = "\n".join(summary.to_lines())
    print(message)

    if notify:
        await notification_service.send_alert(
            "Georgia DeKalb run",
            message,
            tags={"mode": "pipeline" if pipe else "scraper-only"},
        )

    log.info("Georgia / DeKalb run complete")
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run Georgia / DeKalb County Magistrate Court dispossessory scraper. "
            "Default: scraper-only proof. Add --pipe to send filings through "
            "BatchData / GHL / Bland once approved."
        )
    )
    parser.add_argument("--max-cases", type=int, default=200)
    parser.add_argument("--lookback-days", type=int, default=2)
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--pipe", action="store_true")
    return parser


def cli() -> int:
    args = _build_parser().parse_args()
    asyncio.run(
        main(
            max_cases=args.max_cases,
            lookback_days=args.lookback_days,
            notify=args.notify,
            pipe=args.pipe,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
