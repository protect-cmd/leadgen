import asyncio
from datetime import date
from models.filing import Filing
from models.contact import RoutingOutcome
from services.dedup_service import (
    is_duplicate,
    insert_filing,
    update_routing,
    update_ghl_id,
    mark_bland_triggered,
    _client,
)

TEST_CASE_NUMBER = "TEST-DEDUP-2026-001"

TEST_FILING = Filing(
    case_number=TEST_CASE_NUMBER,
    tenant_name="Test Tenant",
    property_address="999 Test St, Los Angeles, CA 90001",
    landlord_name="Test Landlord",
    filing_date=date(2026, 4, 30),
    state="CA",
    county="Los Angeles",
    notice_type="Unlawful Detainer",
    source_url="https://www.lacourt.ca.gov/test",
)


def _cleanup():
    _client.table("filings").delete().eq("case_number", TEST_CASE_NUMBER).execute()


def test_new_case_is_not_duplicate():
    _cleanup()
    result = asyncio.run(is_duplicate(TEST_CASE_NUMBER))
    assert result is False


def test_inserted_case_is_duplicate():
    _cleanup()
    asyncio.run(insert_filing(TEST_FILING))
    result = asyncio.run(is_duplicate(TEST_CASE_NUMBER))
    assert result is True
    _cleanup()


def test_update_routing_sets_outcome():
    _cleanup()
    asyncio.run(insert_filing(TEST_FILING))
    outcome = RoutingOutcome(action="proceed", tag="EC-New-Filing", pipeline="residential")
    asyncio.run(update_routing(TEST_CASE_NUMBER, outcome))
    row = _client.table("filings").select("routing_outcome, routed").eq("case_number", TEST_CASE_NUMBER).execute()
    assert row.data[0]["routing_outcome"] == "proceed"
    assert row.data[0]["routed"] is True
    _cleanup()


def test_update_ghl_id():
    _cleanup()
    asyncio.run(insert_filing(TEST_FILING))
    asyncio.run(update_ghl_id(TEST_CASE_NUMBER, "ghl-123"))
    row = _client.table("filings").select("ghl_contact_id").eq("case_number", TEST_CASE_NUMBER).execute()
    assert row.data[0]["ghl_contact_id"] == "ghl-123"
    _cleanup()


def test_mark_bland_triggered():
    _cleanup()
    asyncio.run(insert_filing(TEST_FILING))
    asyncio.run(mark_bland_triggered(TEST_CASE_NUMBER))
    row = _client.table("filings").select("bland_triggered").eq("case_number", TEST_CASE_NUMBER).execute()
    assert row.data[0]["bland_triggered"] is True
    _cleanup()
