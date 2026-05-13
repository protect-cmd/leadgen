from __future__ import annotations

from datetime import date

from models.filing import Filing
from scripts.proof_maricopa_addresses import format_proof_rows
from scrapers.arizona.maricopa_assessor import AddressMatchResult, ParcelRecord


def test_format_proof_rows_includes_case_level_match_status():
    filings = [
        Filing(
            case_number="CC2026121247",
            tenant_name="Tenant Name",
            property_address="123 W MAIN ST PHOENIX 85001",
            landlord_name="HARMONY AT THE PARK 3",
            filing_date=date(2026, 5, 11),
            court_date=date(2026, 5, 15),
            state="AZ",
            county="Maricopa",
            notice_type="Eviction Action Hearing",
            source_url="https://example.com/case",
        )
    ]
    matches = {
        "CC2026121247": AddressMatchResult(
            status="single_match",
            query_variant="HARMONY AT THE PARK 3",
            records=[
                ParcelRecord(
                    apn="123-45-678",
                    owner_name="HARMONY AT THE PARK 3",
                    physical_address="123 W MAIN ST PHOENIX 85001",
                    mailing_address="",
                    physical_city="PHOENIX",
                    physical_zip="85001",
                    jurisdiction="PHOENIX",
                )
            ],
        )
    }

    lines = format_proof_rows(filings, matches)

    assert lines == [
        "CC2026121247 | single_match | HARMONY AT THE PARK 3 | Tenant Name | 123 W MAIN ST PHOENIX 85001 | APN 123-45-678"
    ]
