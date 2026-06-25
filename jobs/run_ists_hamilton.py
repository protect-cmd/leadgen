# jobs/run_ists_hamilton.py
"""ISTS sub-project A3 run (Hamilton OH / Cincinnati). Mirrors run_ists_franklin.

    python -m jobs.run_ists_hamilton --dry-run   # print + metrics, no DB write
    python -m jobs.run_ists_hamilton             # also upserts to ists_judgments

The scraper enumerates eviction cases by hearing date over a wide lookback and
windows client-side on the judgment (disposition) date, so --lookback-days tunes
the hearing scan, not the judgment window.
"""
from __future__ import annotations
import argparse
import asyncio
import logging
import sys
from collections import Counter
from datetime import date
from pathlib import Path

# Allow running as a plain script (python jobs/run_ists_hamilton.py) under the
# scheduler, not just `python -m jobs.run_ists_hamilton`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scrapers.ohio.hamilton_judgments import HamiltonJudgmentScraper, HEARING_LOOKBACK_DAYS
from services.ists_prior_work import annotate_prior_work
from services import ists_store

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ists.hamilton")


def _metrics(records, scanned: int) -> str:
    if not records:
        return f"no tenant-lost judgments found (scanned {scanned} cases)"
    buckets: Counter = Counter()
    today = date.today()
    for r in records:
        days = (today - r.judgment_date).days if r.judgment_date else -1
        b = ("3-7" if days <= 7 else "8-14" if days <= 14
             else "15-21" if days <= 21 else "22-30" if days <= 30
             else "31-90" if days <= 90 else "other")
        buckets[b] += 1
    with_addr = sum(1 for r in records if r.property_address)
    prior_phone = sum(1 for r in records if r.prior_phone)
    prior_called = sum(1 for r in records if r.prior_bland_status)
    return (f"records={len(records)} | scanned={scanned} | full_address={with_addr}/{len(records)} | "
            f"judgment-date buckets={dict(buckets)} | "
            f"prior_phone={prior_phone} | prior_called={prior_called}")


async def main(dry_run: bool, lookback_days: int) -> None:
    scraper = HamiltonJudgmentScraper(hearing_lookback_days=lookback_days)
    records = scraper.scrape()  # sync (plain requests, no browser)
    if scraper.last_error:
        log.error("scrape error: %s", scraper.last_error)
    records = await annotate_prior_work(records)
    log.info("METRICS: %s", _metrics(records, scraper.scanned))
    if dry_run:
        for r in records[:25]:
            log.info("DRY %s | %s | %s | jdate=%s | disp=%s | prior_phone=%s",
                     r.case_number, r.defendant_name, r.property_address,
                     r.judgment_date, r.disposition_desc, r.prior_phone)
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
    ap.add_argument("--lookback-days", type=int, default=HEARING_LOOKBACK_DAYS,
                    help="hearing-date scan window (not the judgment window)")
    args = ap.parse_args()
    asyncio.run(main(args.dry_run, args.lookback_days))
