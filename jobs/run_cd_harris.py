# jobs/run_cd_harris.py
"""Cosner Drake — Harris debt-claim filings (Cases Filed). Verification harness.

    python -m jobs.run_cd_harris --dry-run                  # scrape + metrics, no DB
    python -m jobs.run_cd_harris --dry-run --lookback 5     # widen the lookback window

This is the first Cosner Drake build step: prove the Harris JP
'Cases Filed / Debt Claim' extract is date-enumerable AND address-complete for
individual defendants before any storage/enrichment is built on top of it. The
GP audit verified the *Judgments* x Debt Claim extract was 100% address-complete;
this harness confirms the same for the *filings* extract.

Only --dry-run is implemented for now: it scrapes the window and prints
verification metrics (volume, address-completeness, individual-defendant share,
sample creditors). The store/enrich path is added in a later step once these
numbers justify it.
"""
from __future__ import annotations
import argparse
import asyncio
import logging
from collections import Counter
from datetime import date, timedelta

from pipeline.gates import gate_address, gate_name
from scrapers.texas.harris_debt_claims import (
    HarrisDebtClaimScraper,
    TX_ANSWER_WINDOW_DAYS,
)

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


async def main(dry_run: bool, lookback: int) -> None:
    if not dry_run:
        raise SystemExit(
            "Only --dry-run is implemented. Storage/enrichment is a later step "
            "pending the verification numbers this harness produces."
        )

    scraper = HarrisDebtClaimScraper(lookback_days=lookback)
    filings = await scraper.scrape()
    log.info("METRICS: %s", _metrics(filings))

    today = date.today()
    creditors: Counter = Counter(f.landlord_name for f in filings if f.landlord_name)
    log.info("top creditors: %s", dict(creditors.most_common(10)))

    for f in filings[:25]:
        deadline = (f.filing_date + timedelta(days=TX_ANSWER_WINDOW_DAYS)
                    if f.filing_date else None)
        flags = "".join((
            "A" if gate_address(f.property_address) else "-",
            "N" if gate_name(f.tenant_name) else "-",
        ))
        log.info("DRY [%s] %s | %s | %s | creditor=%s | filed=%s | answer-by=%s",
                 flags, f.case_number, f.tenant_name, f.property_address,
                 f.landlord_name, f.filing_date, deadline)
    log.info("dry-run: %d filings NOT written", len(filings))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--lookback", type=int, default=3,
                    help="business-day lookback window (default 3)")
    args = ap.parse_args()
    asyncio.run(main(args.dry_run, args.lookback))
