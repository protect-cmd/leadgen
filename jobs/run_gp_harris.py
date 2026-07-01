# jobs/run_gp_harris.py
"""Garnish Proof — Harris debt-claim default judgments. NOT wired into daily_scheduler.

    python -m jobs.run_gp_harris --dry-run        # scrape + filter + metrics, no DB write
    python -m jobs.run_gp_harris                  # upsert default judgments to garnishment_orders
    python -m jobs.run_gp_harris --include-agreed # also keep agreed judgments (default: default-only)

Pulls the Harris JP 'Judgments Entered / Debt Claim' extract, keeps judgments
entered against the consumer (address-gated by the parser), filters to the
default-judgment subset (the prime Garnish Proof lead), maps to the GP storage
shape, and upserts to garnishment_orders for routing under the garnish-proof-lead tag.
"""
from __future__ import annotations
import argparse
import asyncio
import logging
from collections import Counter
from datetime import date

from scrapers.texas.harris_debt_judgments import (
    HarrisDebtJudgmentScraper,
    is_default_judgment,
    to_garnishment_record,
)
from services import gp_store

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("gp.harris")


def _metrics(records) -> str:
    if not records:
        return "no debt-claim judgments found"
    buckets: Counter = Counter()
    today = date.today()
    for r in records:
        days = (today - r.judgment_date).days if r.judgment_date else -1
        b = ("0-7" if 0 <= days <= 7 else "8-14" if days <= 14
             else "15-30" if days <= 30 else "older" if days > 30 else "unknown")
        buckets[b] += 1
    defaults = sum(1 for r in records if is_default_judgment(r))
    return (f"defendant-lost judgments={len(records)} | default={defaults} | "
            f"judgment-date buckets={dict(buckets)}")


async def main(dry_run: bool, include_agreed: bool) -> None:
    scraper = HarrisDebtJudgmentScraper()
    records = await scraper.scrape()
    if scraper.last_error:
        log.error("scrape error: %s", scraper.last_error)
    log.info("METRICS: %s", _metrics(records))

    selected = records if include_agreed else [r for r in records if is_default_judgment(r)]
    log.info("selected %d %s judgments", len(selected),
             "defendant-lost" if include_agreed else "default")

    gp_records = [to_garnishment_record(r) for r in selected]

    if dry_run:
        for gr in gp_records[:25]:
            log.info("DRY %s | %s | %s | creditor=%s | jdate=%s | vacate-by=%s",
                     gr.case_number, gr.debtor_name, gr.debtor_address,
                     gr.creditor_name, gr.filing_date, gr.exemption_deadline)
        log.info("dry-run: %d records NOT written", len(gp_records))
        return

    existing = await gp_store.existing_case_numbers([gr.case_number for gr in gp_records])
    new = [gr for gr in gp_records if gr.case_number not in existing]
    for gr in new:
        await gp_store.upsert_order(gr)
    log.info("stored %d new (skipped %d already present)", len(new), len(gp_records) - len(new))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--include-agreed", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(args.dry_run, args.include_agreed))
