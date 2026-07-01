# jobs/run_ists_sarasota.py
"""ISTS — Sarasota County eviction judgments. Mirrors run_ists_harris / run_ists_franklin.

    python -m jobs.run_ists_sarasota --dry-run               # scrape + metrics, no DB
    python -m jobs.run_ists_sarasota --dry-run --lookback 7  # wider judgment window
    python -m jobs.run_ists_sarasota                         # scrape -> annotate -> upsert

Source: Sarasota County ClerkNet 3.0, Civil / Evictions. Detects "JUDGMENT - RECORDED"
docket entries within the judgment_lookback window. Address via OCR of Complaint PDF.
"""
from __future__ import annotations
import argparse
import asyncio
import logging
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scrapers.florida.sarasota_ists import SarasotaISTSScraper
from services.ists_prior_work import annotate_prior_work
from services import ists_store

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ists.sarasota")


def _metrics(records) -> str:
    if not records:
        return "no tenant-lost judgments found"
    buckets: Counter = Counter()
    today = date.today()
    for r in records:
        days = (today - r.judgment_date).days if r.judgment_date else -1
        b = ("3-7" if days <= 7 else "8-14" if days <= 14
             else "15-21" if days <= 21 else "22-30" if days <= 30
             else "31-90" if days <= 90 else "other")
        buckets[b] += 1
    prior_phone = sum(1 for r in records if r.prior_phone)
    prior_called = sum(1 for r in records if r.prior_bland_status)
    return (
        f"records={len(records)} | judgment-date buckets={dict(buckets)} | "
        f"prior_phone={prior_phone} | prior_called={prior_called}"
    )


async def main(dry_run: bool, judgment_lookback: int) -> None:
    scraper = SarasotaISTSScraper(judgment_lookback_days=judgment_lookback)
    records = await scraper.scrape()
    if scraper.last_error:
        log.error("scrape error: %s", scraper.last_error)
    records = await annotate_prior_work(records)
    log.info("METRICS: %s", _metrics(records))

    if dry_run:
        for r in records[:25]:
            log.info(
                "DRY %s | %s | %s | jdate=%s | prior_phone=%s",
                r.case_number, r.defendant_name, r.property_address,
                r.judgment_date, r.prior_phone,
            )
        log.info("dry-run: %d records NOT written", len(records))
        return

    existing = await ists_store.existing_case_numbers([r.case_number for r in records])
    new = [r for r in records if r.case_number not in existing]
    for r in new:
        await ists_store.upsert_judgment(r)
    log.info("stored %d new (skipped %d already present)", len(new), len(records) - len(new))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--lookback", type=int, default=3,
                    help="judgment lookback window in days (default 3)")
    args = ap.parse_args()
    asyncio.run(main(args.dry_run, args.lookback))
