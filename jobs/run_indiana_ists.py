# jobs/run_indiana_ists.py
"""ISTS Indiana run -- statewide MyCase eviction judgment scrape.

Searches cases filed today-25d to today-14d, filters by judgment events
entered within the last 7 days, converts Filing -> JudgmentRecord, then
upserts to ists_judgments -- matching jobs/run_ists_harris.py and
jobs/run_ists_franklin.py.

    python -m jobs.run_indiana_ists --dry-run   # print + metrics, no DB write
    python -m jobs.run_indiana_ists             # upserts to ists_judgments

Runtime: ~57 min statewide (815 cases @ avg 3 s/detail fetch).
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

from dotenv import load_dotenv

load_dotenv()

from models.filing import Filing
from models.judgment import JudgmentRecord
from scrapers.indiana.mycase_ists import IndianaISTSScraper
from services.ists_prior_work import annotate_prior_work

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("ists.indiana")


def _to_judgment(f: Filing) -> JudgmentRecord:
    """Convert an Indiana ISTS Filing to a JudgmentRecord for ists_judgments."""
    return JudgmentRecord(
        case_number=f.case_number,
        defendant_name=f.tenant_name,
        property_address=f.property_address,
        plaintiff_name=f.landlord_name,
        state=f.state,
        county=f.county,
        judgment_date=f.judgment_date,
        judgment_in_favor_of="Plaintiff",
        source_url=f.source_url,
    )


def _metrics(records: list[JudgmentRecord]) -> str:
    if not records:
        return "no tenant-lost judgments found"
    buckets: Counter = Counter()
    today = date.today()
    for r in records:
        days = (today - r.judgment_date).days if r.judgment_date else -1
        b = (
            "3-7" if days <= 7 else
            "8-14" if days <= 14 else
            "15-21" if days <= 21 else
            "22-30" if days <= 30 else
            "31-90" if days <= 90 else
            "other"
        )
        buckets[b] += 1
    prior_phone = sum(1 for r in records if r.prior_phone)
    prior_called = sum(1 for r in records if r.prior_bland_status)
    return (
        "records=%d | judgment-date buckets=%s | prior_phone=%d | prior_called=%d"
        % (len(records), dict(buckets), prior_phone, prior_called)
    )


async def main(dry_run: bool) -> None:
    scraper = IndianaISTSScraper()
    filings = await scraper.scrape()
    if scraper.last_error:
        log.error("scrape error: %s", scraper.last_error)

    records = [_to_judgment(f) for f in filings]

    try:
        records = await annotate_prior_work(records)
    except Exception as exc:
        # Supabase unavailable (e.g. local dry-run with fake credentials).
        # prior_phone / prior_bland_status stay at their defaults (False / None).
        log.warning("annotate_prior_work skipped: %s", exc)

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

    # ists_store imported here so fake-credential dry-runs never initialise it.
    from services import ists_store
    existing = await ists_store.existing_case_numbers([r.case_number for r in records])
    new = [r for r in records if r.case_number not in existing]
    for r in new:
        await ists_store.upsert_judgment(r)
    log.info(
        "stored %d new (skipped %d already present)",
        len(new), len(records) - len(new),
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    asyncio.run(main(ap.parse_args().dry_run))
