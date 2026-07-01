# jobs/run_cd_harris.py
"""Cosner Drake — Harris debt-claim filings (Cases Filed). NOT wired into daily_scheduler.

    python -m jobs.run_cd_harris --dry-run                  # scrape + metrics, no DB
    python -m jobs.run_cd_harris --dry-run --lookback 5     # widen the lookback window
    python -m jobs.run_cd_harris                            # scrape -> gate -> upsert to cosner_filings

Source: the Harris JP 'Cases Filed / Debt Claim' extract (pre-judgment debt
lawsuits), proven date-enumerable + address-complete. Live runs gate each filing
to an individual defendant with a complete home address (gate_name + gate_address),
map to the Cosner Drake storage shape (answer_deadline = filing_date + 30d), and
upsert new case numbers to cosner_filings for SearchBug enrichment + routing under
the cosner-drake-lead tag.

Requires migration 025_cosner_filings.sql applied to the live DB.
"""
from __future__ import annotations
import argparse
import asyncio
import logging
import sys
from collections import Counter
from pathlib import Path

# The daily scheduler invokes this as `python jobs/run_cd_harris.py` (script mode),
# which puts jobs/ — not the repo root — on sys.path, so the absolute imports below
# (pipeline.*, scrapers.*, services.*) fail with ModuleNotFoundError. Add the repo
# root, matching run_texas.py et al., so the scheduled Cosner job can import.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.gates import gate_address, gate_name
from scrapers.texas.harris_debt_claims import (
    HarrisDebtClaimScraper,
    to_cosner_filing,
    TX_ANSWER_WINDOW_DAYS,
)
from services import cd_store

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("cd.harris")


def _metrics(filings) -> str:
    if not filings:
        return "no debt-claim filings found"
    total = len(filings)
    addr_ok = sum(1 for f in filings if gate_address(f.property_address))
    name_ok = sum(1 for f in filings if gate_name(f.tenant_name))
    enrichable = sum(
        1 for f in filings
        if gate_address(f.property_address) and gate_name(f.tenant_name)
    )
    file_dates: Counter = Counter(str(f.filing_date) for f in filings)
    return (
        f"filings={total} | address-complete={addr_ok} ({addr_ok * 100 // total}%) | "
        f"individual-defendant={name_ok} ({name_ok * 100 // total}%) | "
        f"enrichable (both)={enrichable} ({enrichable * 100 // total}%) | "
        f"filing-date spread={dict(file_dates)}"
    )


def _enrichable(filings):
    """Individual defendants with a complete home address — the storable leads."""
    return [f for f in filings
            if gate_name(f.tenant_name) and gate_address(f.property_address)]


async def main(dry_run: bool, lookback: int) -> None:
    scraper = HarrisDebtClaimScraper(lookback_days=lookback)
    filings = await scraper.scrape()
    log.info("METRICS: %s", _metrics(filings))

    creditors: Counter = Counter(f.landlord_name for f in filings if f.landlord_name)
    log.info("top creditors: %s", dict(creditors.most_common(10)))

    selected = _enrichable(filings)
    log.info("gated to %d enrichable filings (of %d scraped)", len(selected), len(filings))
    records = [to_cosner_filing(f) for f in selected]

    if dry_run:
        for cf in records[:25]:
            log.info("DRY %s | %s | %s | creditor=%s | filed=%s | answer-by=%s",
                     cf.case_number, cf.defendant_name, cf.defendant_address,
                     cf.creditor_name, cf.filing_date, cf.answer_deadline)
        log.info("dry-run: %d filings NOT written", len(records))
        return

    existing = await cd_store.existing_case_numbers([cf.case_number for cf in records])
    new = [cf for cf in records if cf.case_number not in existing]
    for cf in new:
        await cd_store.upsert_filing(cf)
    log.info("stored %d new (skipped %d already present)", len(new), len(records) - len(new))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--lookback", type=int, default=3,
                    help="business-day lookback window (default 3)")
    args = ap.parse_args()
    asyncio.run(main(args.dry_run, args.lookback))
