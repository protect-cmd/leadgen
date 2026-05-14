from __future__ import annotations

from datetime import date

import pytest

from models.filing import Filing
from scripts.push_franklin_filings import PushSummary, push_filings_to_supabase


def _filing(case_number: str) -> Filing:
    return Filing(
        case_number=case_number,
        tenant_name="Test Tenant",
        property_address="123 Test St, Columbus, OH 43229",
        landlord_name="Test Landlord",
        filing_date=date(2026, 5, 1),
        state="OH",
        county="Franklin",
        notice_type="Civil F.E.D. Eviction",
        source_url="https://example.test/franklin.csv",
    )


@pytest.mark.asyncio
async def test_push_filings_to_supabase_inserts_only_non_duplicates():
    filings = [_filing("A"), _filing("B"), _filing("C")]
    existing = {"B"}
    inserted: list[str] = []

    async def is_duplicate(case_number: str) -> bool:
        return case_number in existing

    async def insert_filing(filing: Filing) -> None:
        inserted.append(filing.case_number)

    summary = await push_filings_to_supabase(
        filings,
        is_duplicate=is_duplicate,
        insert_filing=insert_filing,
    )

    assert summary == PushSummary(received=3, inserted=2, duplicates=1)
    assert inserted == ["A", "C"]
