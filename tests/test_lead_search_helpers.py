"""Tests for the search/notes/mark-called helpers added in Spec 4."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.dedup_service import search_leads, _sanitize_search_query


def test_sanitize_search_query_strips_filter_breakers():
    assert _sanitize_search_query("ma,ria%g") == "mariag"
    assert _sanitize_search_query("  trim  ") == "trim"
    assert _sanitize_search_query("o'brien") == "o'brien"
    assert _sanitize_search_query("name-with-dash") == "name-with-dash"


def test_sanitize_search_query_handles_empty():
    assert _sanitize_search_query("") == ""
    assert _sanitize_search_query("   ") == ""
    assert _sanitize_search_query(None) == ""  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_search_leads_returns_empty_on_short_query():
    """Queries under 2 chars return [] without hitting Supabase."""
    with patch("services.dedup_service._client") as mock_client:
        result = await search_leads("a")
    assert result == []
    mock_client.table.assert_not_called()


@pytest.mark.asyncio
async def test_search_leads_strips_unsafe_chars_before_query():
    """%,'\" — PostgREST filter-breakers — must be stripped from q."""
    client = MagicMock()
    contact_chain = MagicMock()
    contact_chain.select.return_value.or_.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    filing_chain = MagicMock()
    filing_chain.select.return_value.or_.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    client.table.side_effect = lambda name: contact_chain if name == "lead_contacts" else filing_chain
    with patch("services.dedup_service._client", client):
        await search_leads("ma,ria%")
    # The .or_() call gets a filter string; ensure no raw comma/% from input
    contact_or_calls = contact_chain.select.return_value.or_.call_args_list
    assert contact_or_calls, "or_() was not called"
    filter_str = contact_or_calls[0].args[0]
    assert "maria" in filter_str
    assert ",ria" not in filter_str  # no leak of raw comma


@pytest.mark.asyncio
async def test_search_leads_merges_and_dedupes_by_case_number():
    """Same case_number appearing in both contact + filing matches returns once."""
    contact_rows = [
        {"case_number": "C-1", "track": "ng", "contact_name": "Maria Garcia",
         "phone": "5551234567", "filings": {"filing_date": "2026-05-29",
         "property_address": "1 Main", "tenant_name": "Maria Garcia",
         "state": "TX", "county": "Harris", "court_date": None}},
    ]
    filing_rows = [
        {"case_number": "C-1", "tenant_name": "Maria Garcia",
         "property_address": "1 Main", "filing_date": "2026-05-29",
         "state": "TX", "county": "Harris", "court_date": None,
         "lead_contacts": []},
        {"case_number": "C-2", "tenant_name": "Maria Lopez",
         "property_address": "2 Main", "filing_date": "2026-05-28",
         "state": "TX", "county": "Harris", "court_date": None,
         "lead_contacts": []},
    ]
    client = MagicMock()
    contact_chain = MagicMock()
    contact_chain.select.return_value.or_.return_value.limit.return_value.execute.return_value = MagicMock(data=contact_rows)
    filing_chain = MagicMock()
    filing_chain.select.return_value.or_.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=filing_rows)
    client.table.side_effect = lambda name: contact_chain if name == "lead_contacts" else filing_chain
    with patch("services.dedup_service._client", client):
        result = await search_leads("maria")
    case_numbers = [r["case_number"] for r in result]
    assert case_numbers.count("C-1") == 1, "C-1 should appear once after merge"
    assert "C-2" in case_numbers


@pytest.mark.asyncio
async def test_search_leads_sorts_by_filing_date_desc():
    """More-recent filings should appear first in the merged list."""
    contact_rows = []
    filing_rows = [
        {"case_number": "OLD", "tenant_name": "Maria",
         "property_address": "x", "filing_date": "2026-05-01",
         "state": "TX", "county": "Harris", "court_date": None,
         "lead_contacts": []},
        {"case_number": "NEW", "tenant_name": "Maria",
         "property_address": "x", "filing_date": "2026-05-29",
         "state": "TX", "county": "Harris", "court_date": None,
         "lead_contacts": []},
    ]
    client = MagicMock()
    contact_chain = MagicMock()
    contact_chain.select.return_value.or_.return_value.limit.return_value.execute.return_value = MagicMock(data=contact_rows)
    filing_chain = MagicMock()
    filing_chain.select.return_value.or_.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=filing_rows)
    client.table.side_effect = lambda name: contact_chain if name == "lead_contacts" else filing_chain
    with patch("services.dedup_service._client", client):
        result = await search_leads("maria")
    assert [r["case_number"] for r in result] == ["NEW", "OLD"]


@pytest.mark.asyncio
async def test_search_leads_respects_limit():
    """Returned list never exceeds the limit parameter."""
    filing_rows = [
        {"case_number": f"C-{i}", "tenant_name": "X",
         "property_address": "x", "filing_date": "2026-05-29",
         "state": "TX", "county": "Harris", "court_date": None,
         "lead_contacts": []}
        for i in range(30)
    ]
    client = MagicMock()
    contact_chain = MagicMock()
    contact_chain.select.return_value.or_.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    filing_chain = MagicMock()
    filing_chain.select.return_value.or_.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=filing_rows)
    client.table.side_effect = lambda name: contact_chain if name == "lead_contacts" else filing_chain
    with patch("services.dedup_service._client", client):
        result = await search_leads("xx", limit=10)
    assert len(result) == 10
