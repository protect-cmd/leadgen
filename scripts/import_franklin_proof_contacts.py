from __future__ import annotations

import argparse
import asyncio
import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from models.contact import EnrichedContact
from models.filing import Filing
from pipeline.qualification import classify_lead
from services import dedup_service, language_service


@dataclass(frozen=True)
class ImportSummary:
    csv_rows: int
    contacts_imported: int
    missing_filings: int


def _truthy(value: str | bool | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def proof_rows_to_contacts(
    rows: list[dict[str, str]],
    filings_by_case: dict[str, Filing],
) -> list[EnrichedContact]:
    contacts: list[EnrichedContact] = []
    for row in rows:
        case_number = (row.get("case_number") or "").strip()
        filing = filings_by_case.get(case_number)
        if filing is None:
            continue
        phone = (row.get("phone") or "").strip()
        if not phone or not _truthy(row.get("callable")):
            continue
        contacts.append(
            EnrichedContact(
                filing=filing,
                track="ng",
                phone=phone,
                email=(row.get("email") or "").strip() or None,
                property_type="residential",
            )
        )
    return contacts


async def _load_filings(case_numbers: list[str]) -> dict[str, Filing]:
    rows = (
        dedup_service._client.table("filings")
        .select(
            "case_number,tenant_name,property_address,landlord_name,filing_date,"
            "court_date,state,county,notice_type,source_url"
        )
        .in_("case_number", case_numbers)
        .execute()
        .data
    )
    filings: dict[str, Filing] = {}
    for row in rows:
        filings[row["case_number"]] = Filing(
            case_number=row["case_number"],
            tenant_name=row["tenant_name"],
            property_address=row["property_address"],
            landlord_name=row["landlord_name"],
            filing_date=date.fromisoformat(row["filing_date"]),
            court_date=date.fromisoformat(row["court_date"]) if row.get("court_date") else None,
            state=row["state"],
            county=row["county"],
            notice_type=row["notice_type"],
            source_url=row["source_url"],
        )
    return filings


async def import_contacts_from_csv(csv_path: Path) -> ImportSummary:
    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    case_numbers = [(row.get("case_number") or "").strip() for row in rows if row.get("case_number")]
    filings_by_case = await _load_filings(case_numbers)
    contacts = proof_rows_to_contacts(rows, filings_by_case)

    for contact in contacts:
        language_hint = language_service.language_hint_for_name(contact.filing.tenant_name)
        contact.language_hint = language_hint
        outcome = classify_lead(
            state=contact.filing.state,
            property_address=contact.filing.property_address,
            filing_date=contact.filing.filing_date,
            property_type=contact.property_type,
        )
        await dedup_service.update_classification(contact.filing.case_number, outcome)
        if language_hint:
            await dedup_service.update_language_hint(contact.filing.case_number, language_hint)
        await dedup_service.upsert_contact_enrichment(contact)

    return ImportSummary(
        csv_rows=len(rows),
        contacts_imported=len(contacts),
        missing_filings=len(set(case_numbers) - set(filings_by_case)),
    )


async def main_async(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import DNC-clear Franklin tenant proof contacts into Supabase lead_contacts."
    )
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--yes-write-supabase", action="store_true")
    args = parser.parse_args(argv)
    if not args.yes_write_supabase:
        parser.error("--yes-write-supabase is required because this writes to Supabase")

    load_dotenv()
    summary = await import_contacts_from_csv(args.csv_path)
    print("Franklin proof contact import")
    print(f"CSV rows: {summary.csv_rows}")
    print(f"Contacts imported: {summary.contacts_imported}")
    print(f"Missing filings: {summary.missing_filings}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
