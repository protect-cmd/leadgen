# jobs/run_cd_sarasota.py
"""Cosner Drake — Sarasota County Small Claims debt filings (Cases Filed).

    python -m jobs.run_cd_sarasota --dry-run               # scrape + metrics, no DB
    python -m jobs.run_cd_sarasota --dry-run --lookback 7  # widen the window
    python -m jobs.run_cd_sarasota                         # scrape -> gate -> upsert

Source: Sarasota County ClerkNet 3.0, Civil / Small Claims case type.
Address pulled from Summons Issued PDF (embedded text, no OCR).
Amount pulled from Statement of Claim PDF.
Answer deadline = filing_date + 30 days (FL Small Claims contact window).
"""
from __future__ import annotations
import argparse
import asyncio
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.gates import gate_address, gate_name
from scrapers.florida.sarasota_cosner import SarasotaCosnerScraper
from services import cd_store

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("cd.sarasota")


def _metrics(filings) -> str:
    if not filings:
        return "no small-claims filings found"
    total = len(filings)
    addr_ok = sum(1 for f in filings if gate_address(f.defendant_address))
    name_ok = sum(1 for f in filings if gate_name(f.defendant_name))
    enrichable = sum(
        1 for f in filings
        if gate_address(f.defendant_address) and gate_name(f.defendant_name)
    )
    file_dates: Counter = Counter(str(f.filing_date) for f in filings)
    return (
        f"filings={total} | address-complete={addr_ok} ({addr_ok * 100 // total}%) | "
        f"individual-defendant={name_ok} ({name_ok * 100 // total}%) | "
        f"enrichable (both)={enrichable} ({enrichable * 100 // total}%) | "
        f"filing-date spread={dict(file_dates)}"
    )


def _enrichable(filings):
    return [
        f for f in filings
        if gate_name(f.defendant_name) and gate_address(f.defendant_address)
    ]


async def main(dry_run: bool, lookback: int) -> None:
    scraper = SarasotaCosnerScraper(lookback_days=lookback)
    filings = await scraper.scrape()
    log.info("METRICS: %s", _metrics(filings))

    creditors: Counter = Counter(f.creditor_name for f in filings if f.creditor_name)
    log.info("top creditors: %s", dict(creditors.most_common(10)))

    selected = _enrichable(filings)
    log.info("gated to %d enrichable filings (of %d scraped)", len(selected), len(filings))

    if dry_run:
        for cf in selected[:25]:
            log.info(
                "DRY %s | %s | %s | creditor=%s | amount=$%s | filed=%s | answer-by=%s",
                cf.case_number, cf.defendant_name, cf.defendant_address,
                cf.creditor_name, cf.debt_amount, cf.filing_date, cf.answer_deadline,
            )
        log.info("dry-run: %d filings NOT written", len(selected))
        return

    existing = await cd_store.existing_case_numbers([cf.case_number for cf in selected])
    new = [cf for cf in selected if cf.case_number not in existing]
    for cf in new:
        await cd_store.upsert_filing(cf)
    log.info("stored %d new (skipped %d already present)", len(new), len(selected) - len(new))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--lookback", type=int, default=3,
                    help="day lookback window (default 3)")
    args = ap.parse_args()
    asyncio.run(main(args.dry_run, args.lookback))
