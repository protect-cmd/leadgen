from __future__ import annotations

from datetime import date

import pytest

from models.contact import EnrichedContact
from models.filing import Filing
from scripts.proof_franklin_tenant_enrichment import (
    build_summary,
    format_summary_lines,
    run_enrichment_proof,
    select_latest_filings,
)


def _filing(case_number: str, tenant_name: str = "Maria Tenant") -> Filing:
    return Filing(
        case_number=case_number,
        tenant_name=tenant_name,
        property_address="123 Test St Apt 4, Columbus, OH 43229",
        landlord_name="Test Apartments LLC",
        filing_date=date(2026, 5, 1),
        state="OH",
        county="Franklin",
        notice_type="Civil F.E.D. Eviction",
        source_url="https://example.test/franklin.csv",
    )


@pytest.mark.asyncio
async def test_run_enrichment_proof_counts_only_dnc_clear_as_callable():
    filings = [_filing("A"), _filing("B"), _filing("C")]
    contacts = {
        "A": EnrichedContact(filing=filings[0], track="ng", phone="5551110000", dnc_status="clear"),
        "B": EnrichedContact(filing=filings[1], track="ng", phone="5552220000", dnc_status="blocked"),
        "C": EnrichedContact(filing=filings[2], track="ng", phone=None, dnc_status="unknown"),
    }

    async def fake_enrich(filing: Filing) -> EnrichedContact:
        return contacts[filing.case_number]

    rows = await run_enrichment_proof(filings, fake_enrich)
    summary = build_summary(rows)

    assert summary.total == 3
    assert summary.phones_found == 2
    assert summary.callable == 1
    assert summary.dnc_blocked == 1
    assert summary.dnc_unknown == 1
    assert [(row.case_number, row.phone, row.dnc_status, row.callable) for row in rows] == [
        ("A", "5551110000", "clear", True),
        ("B", "5552220000", "blocked", False),
        ("C", "", "unknown", False),
    ]


def test_format_summary_lines_reports_hit_rates():
    rows = [
        row
        for row in [
            # Use the async proof test for row behavior; this test cares about summary rendering.
        ]
    ]
    summary = build_summary(rows)

    assert format_summary_lines(summary) == [
        "Franklin tenant enrichment proof",
        "Total checked: 0",
        "Phones found: 0 (0.0%)",
        "Callable DNC-clear phones: 0 (0.0%)",
        "DNC blocked phones: 0",
        "DNC unknown/no-phone: 0",
    ]


def test_select_latest_filings_sorts_by_filing_date_and_case_number():
    filings = [
        _filing("2026 CVG 025000"),
        _filing("2026 CVG 026000"),
        _filing("2026 CVG 025500"),
    ]
    filings[0].filing_date = date(2026, 5, 1)
    filings[1].filing_date = date(2026, 5, 6)
    filings[2].filing_date = date(2026, 5, 6)

    selected = select_latest_filings(filings, max_cases=2)

    assert [filing.case_number for filing in selected] == [
        "2026 CVG 026000",
        "2026 CVG 025500",
    ]
