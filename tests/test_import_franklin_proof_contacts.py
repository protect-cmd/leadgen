from __future__ import annotations

from datetime import date

import pytest

from models.contact import EnrichedContact
from models.filing import Filing
from scripts.import_franklin_proof_contacts import proof_rows_to_contacts


@pytest.mark.asyncio
async def test_proof_rows_to_contacts_imports_only_callable_rows():
    filing = Filing(
        case_number="2026 CVG 027160",
        tenant_name="RICHARD THOMPSON",
        property_address="123 Test St, Columbus, OH 43229",
        landlord_name="Test Landlord",
        filing_date=date(2026, 5, 12),
        state="OH",
        county="Franklin",
        notice_type="Civil F.E.D. Eviction",
        source_url="https://example.test",
    )
    rows = [
        {
            "case_number": filing.case_number,
            "phone": "5551110000",
            "email": "tenant@example.test",
            "dnc_status": "clear",
            "dnc_source": "batchdata",
            "callable": "True",
        },
        {
            "case_number": "NOPE",
            "phone": "",
            "email": "",
            "dnc_status": "unknown",
            "dnc_source": "",
            "callable": "False",
        },
    ]

    contacts = proof_rows_to_contacts(rows, {filing.case_number: filing})

    assert contacts == [
        EnrichedContact(
            filing=filing,
            track="ng",
            phone="5551110000",
            email="tenant@example.test",
            property_type="residential",
        )
    ]
