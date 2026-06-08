# jobs/run_ists_harris.py
"""Manual ISTS sub-project A run (Harris). NOT wired into daily_scheduler.

    python -m jobs.run_ists_harris --dry-run   # print + metrics, no DB write
    python -m jobs.run_ists_harris             # also upserts to ists_judgments
"""
from __future__ import annotations
import argparse
import asyncio
import logging
from collections import Counter
from datetime import date

from scrapers.texas.harris_judgments import HarrisJudgmentScraper
from services.ists_prior_work import annotate_prior_work
from services import ists_store

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ists.harris")


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
    return (f"records={len(records)} | judgment-date buckets={dict(buckets)} | "
            f"prior_phone={prior_phone} | prior_called={prior_called}")


async def main(dry_run: bool) -> None:
    scraper = HarrisJudgmentScraper()
    records = await scraper.scrape()
    if scraper.last_error:
        log.error("scrape error: %s", scraper.last_error)
    records = await annotate_prior_work(records)
    log.info("METRICS: %s", _metrics(records))
    if dry_run:
        for r in records[:25]:
            log.info("DRY %s | %s | %s | jdate=%s | prior_phone=%s",
                     r.case_number, r.defendant_name, r.property_address,
                     r.judgment_date, r.prior_phone)
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
    asyncio.run(main(ap.parse_args().dry_run))
