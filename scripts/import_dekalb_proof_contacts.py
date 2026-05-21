"""
Import DeKalb County GA yellow-source proof contacts into Supabase.

Reads tmp/dekalb_yellow_enrichment_proof.csv, finds rows with a phone number,
inserts the filing if not already present, and upserts the contact enrichment.

DNC status is carried as-is from the proof CSV (may be "unknown" for SearchBug
phone-only hits that had no address to run through BatchData).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from models.contact import EnrichedContact
from models.filing import Filing
from scrapers.georgia.dekalb import DeKalbDispossessoryScraper
from services import dedup_service, language_service


@dataclass(frozen=True)
class ImportSummary:
    csv_rows: int
    rows_with_phone: int
    filings_inserted: int
    filings_already_existed: int
    contacts_upserted: int


async def run_import(csv_path: Path) -> ImportSummary:
    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("phone") or "").strip()]

    if not rows:
        return ImportSummary(
            csv_rows=0, rows_with_phone=0,
            filings_inserted=0, filings_already_existed=0, contacts_upserted=0,
        )

    target_cases = {r["case_number"].strip() for r in rows}

    # Re-scrape DeKalb to get full Filing objects for these case numbers
    scraper = DeKalbDispossessoryScraper()
    all_filings = scraper.scrape()
    filings_by_case = {f.case_number: f for f in all_filings if f.case_number in target_cases}

    filings_inserted = 0
    filings_existed = 0
    contacts_upserted = 0

    for row in rows:
        case_number = row["case_number"].strip()
        filing = filings_by_case.get(case_number)
        if filing is None:
            print(f"  WARNING: case {case_number} not found in current scrape — skipping")
            continue

        # Insert filing if not already in Supabase
        if await dedup_service.is_duplicate(case_number):
            print(f"  {case_number}: filing already in Supabase")
            filings_existed += 1
        else:
            await dedup_service.insert_filing(filing)
            print(f"  {case_number}: filing inserted")
            filings_inserted += 1

        # Build and upsert the contact
        contact = EnrichedContact(
            filing=filing,
            track="ng",
            phone=row["phone"].strip(),
            dnc_status=row.get("dnc_status", "unknown").strip() or "unknown",
            dnc_source=row.get("dnc_source", "searchbug").strip() or "searchbug",
        )
        language_hint = language_service.language_hint_for_name(filing.tenant_name)
        contact.language_hint = language_hint

        await dedup_service.upsert_contact_enrichment(contact)
        print(f"  {case_number}: contact upserted — phone={contact.phone} dnc={contact.dnc_status}")
        contacts_upserted += 1

    return ImportSummary(
        csv_rows=len(rows),
        rows_with_phone=len(rows),
        filings_inserted=filings_inserted,
        filings_already_existed=filings_existed,
        contacts_upserted=contacts_upserted,
    )


async def main_async(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import DeKalb GA yellow-source proof contacts into Supabase."
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path("tmp/dekalb_yellow_enrichment_proof.csv"),
    )
    parser.add_argument(
        "--yes-write-supabase",
        action="store_true",
        help="Required — this script writes to Supabase.",
    )
    args = parser.parse_args(argv)

    if not args.yes_write_supabase:
        parser.error("--yes-write-supabase required (this writes to Supabase)")

    load_dotenv()

    print(f"Importing from {args.csv}")
    summary = await run_import(args.csv)

    print()
    print("DeKalb proof contact import")
    print(f"  Rows with phone:          {summary.rows_with_phone}")
    print(f"  Filings inserted:         {summary.filings_inserted}")
    print(f"  Filings already existed:  {summary.filings_already_existed}")
    print(f"  Contacts upserted:        {summary.contacts_upserted}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
