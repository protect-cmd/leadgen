"""Tests for the search/notes/mark-called helpers added in Spec 4."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.dedup_service import (
    search_leads,
    _sanitize_search_query,
    add_lead_note,
    list_lead_notes,
    mark_lead_called,
)


def _empty_chain():
    c = MagicMock()
    c.select.return_value.or_.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    return c


def _route(contact, filing, ists=None):
    chains = {"lead_contacts": contact, "filings": filing,
              "ists_judgments": ists if ists is not None else _empty_chain()}
    return lambda name: chains[name]


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
    client.table.side_effect = _route(contact_chain, filing_chain)
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
    client.table.side_effect = _route(contact_chain, filing_chain)
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
    client.table.side_effect = _route(contact_chain, filing_chain)
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
    client.table.side_effect = _route(contact_chain, filing_chain)
    with patch("services.dedup_service._client", client):
        result = await search_leads("xx", limit=10)
    assert len(result) == 10


@pytest.mark.asyncio
async def test_search_leads_includes_ists_judgments_by_phone():
    """An ISTS judgment lead (no lead_contacts/filings row) is findable by phone
    and surfaces with track='ists' and defendant mapped to tenant_name."""
    ists_rows = [{
        "case_number": "264100196540", "defendant_name": "Silvio Gamez",
        "property_address": "20525 Ella Blvd Apt 1306, Spring, TX 77388",
        "phone": "3463710233", "dnc_status": "callable",
        "bland_call_id": "b49872ca", "ghl_contact_id": "emgyUF23",
        "judgment_date": "2026-06-02", "plaintiff_name": "Ella REH LLC",
        "state": "TX", "county": "Harris", "language_hint": None,
    }]
    client = MagicMock()
    contact_chain = _empty_chain()
    filing_chain = MagicMock()
    filing_chain.select.return_value.or_.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    ists_chain = MagicMock()
    ists_chain.select.return_value.or_.return_value.limit.return_value.execute.return_value = MagicMock(data=ists_rows)
    client.table.side_effect = _route(contact_chain, filing_chain, ists_chain)
    with patch("services.dedup_service._client", client):
        result = await search_leads("3463710233")
    assert len(result) == 1
    row = result[0]
    assert row["case_number"] == "264100196540"
    assert row["track"] == "ists"
    assert row["tenant_name"] == "Silvio Gamez"
    assert row["phone"] == "3463710233"
    assert row["bland_status"] == "triggered"
    assert row["landlord_name"] == "Ella REH LLC"
    assert row["judgment_date"] == "2026-06-02"


@pytest.mark.asyncio
async def test_add_lead_note_inserts_with_default_author():
    """A note is INSERTed with the caller-default author and the right fields."""
    client = MagicMock()
    insert_chain = client.table.return_value.insert.return_value
    insert_chain.execute.return_value = MagicMock(
        data=[{"id": 7, "case_number": "C-1", "track": "ng",
               "note_text": "Hello", "author": "caller",
               "created_at": "2026-05-29T20:00:00+00:00"}]
    )
    with patch("services.dedup_service._client", client):
        row = await add_lead_note(case_number="C-1", track="ng", text="Hello")
    assert row["id"] == 7
    assert row["author"] == "caller"
    call_args = client.table.return_value.insert.call_args.args[0]
    assert call_args["case_number"] == "C-1"
    assert call_args["track"] == "ng"
    assert call_args["note_text"] == "Hello"
    assert call_args["author"] == "caller"


@pytest.mark.asyncio
async def test_add_lead_note_rejects_empty_text():
    """Empty / whitespace-only text raises ValueError before any DB call."""
    with patch("services.dedup_service._client") as mock_client:
        with pytest.raises(ValueError, match="empty"):
            await add_lead_note(case_number="C-1", track="ng", text="   ")
    mock_client.table.assert_not_called()


@pytest.mark.asyncio
async def test_add_lead_note_rejects_oversize_text():
    """Text over 2000 chars raises ValueError."""
    with patch("services.dedup_service._client") as mock_client:
        with pytest.raises(ValueError, match="2000"):
            await add_lead_note(case_number="C-1", track="ng", text="x" * 2001)


@pytest.mark.asyncio
async def test_list_lead_notes_returns_rows_in_desc_order():
    """list_lead_notes selects from lead_notes filtered + ordered DESC."""
    rows = [
        {"id": 3, "note_text": "newest", "created_at": "2026-05-29T20:00:00+00:00"},
        {"id": 2, "note_text": "older", "created_at": "2026-05-28T20:00:00+00:00"},
    ]
    client = MagicMock()
    chain = client.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value
    chain.execute.return_value = MagicMock(data=rows)
    with patch("services.dedup_service._client", client):
        out = await list_lead_notes(case_number="C-1", track="ng")
    assert [r["id"] for r in out] == [3, 2]


@pytest.mark.asyncio
async def test_mark_lead_called_updates_timestamp():
    """Sends UPDATE on lead_contacts with last_called_at = now()."""
    client = MagicMock()
    chain = client.table.return_value.update.return_value.eq.return_value.eq.return_value
    chain.execute.return_value = MagicMock(data=[{
        "case_number": "C-1", "track": "ng",
        "last_called_at": "2026-05-29T20:00:00+00:00",
    }])
    with patch("services.dedup_service._client", client):
        ts = await mark_lead_called(case_number="C-1", track="ng")
    assert isinstance(ts, str) and "T" in ts
    update_arg = client.table.return_value.update.call_args.args[0]
    assert "last_called_at" in update_arg
