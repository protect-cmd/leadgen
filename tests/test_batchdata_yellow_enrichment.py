from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from models.contact import EnrichedContact
from models.filing import Filing
from services import batchdata_service
from services.enrichment_cache import EnrichmentCache


def _filing(**kwargs) -> Filing:
    values = {
        "case_number": "TEST-YELLOW-001",
        "tenant_name": "Brett Lilly",
        "property_address": "Cincinnati, OH",
        "landlord_name": "Apex LLC",
        "filing_date": date(2026, 5, 15),
        "state": "OH",
        "county": "Hamilton",
        "notice_type": "Eviction",
        "source_url": "https://example.test",
    }
    values.update(kwargs)
    return Filing(**values)


@pytest.fixture
def mock_cache(tmp_path):
    return EnrichmentCache(db_path=str(tmp_path / "test.db"))


@pytest.fixture(autouse=True)
def set_api_key(monkeypatch):
    monkeypatch.setenv("BATCHDATA_API_KEY", "test-key")
    monkeypatch.setenv("SEARCHBUG_CO_CODE", "test-co")
    monkeypatch.setenv("SEARCHBUG_API_KEY", "test-key")
    monkeypatch.setenv("SEARCHBUG_DAILY_CAP", "100")


@pytest.mark.asyncio
async def test_common_surname_skips_searchbug(mock_cache):
    """Smith (common surname) → SearchBug never called, unenriched contact returned."""
    filing = _filing(tenant_name="JOHN SMITH")

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock) as mock_sb:
        result = await batchdata_service.enrich_tenant_by_name(filing)

    mock_sb.assert_not_called()
    assert result.phone is None
    assert result.track == "ng"


@pytest.mark.asyncio
async def test_cache_hit_skips_searchbug(mock_cache):
    """Cached phone hit → SearchBug never called."""
    filing = _filing(tenant_name="BRETT LILLY")
    mock_cache.set("brett", "lilly", "cincinnati", "oh", "5551234567", None)

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock) as mock_sb:
        result = await batchdata_service.enrich_tenant_by_name(filing)

    mock_sb.assert_not_called()
    assert result.phone == "5551234567"
    assert result.dnc_source == "searchbug"


@pytest.mark.asyncio
async def test_cache_miss_calls_searchbug_and_stores(mock_cache):
    """Cache miss → SearchBug called, result stored in cache."""
    filing = _filing(tenant_name="BRETT LILLY")

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock,
               return_value=("5559876543", None)) as mock_sb:
        result = await batchdata_service.enrich_tenant_by_name(filing)

    mock_sb.assert_called_once()
    assert result.phone == "5559876543"
    # Verify stored in cache
    cached = mock_cache.get("brett", "lilly", "cincinnati", "oh")
    assert cached == ("5559876543", None)


@pytest.mark.asyncio
async def test_searchbug_address_triggers_batchdata(mock_cache):
    """SearchBug returns address → enrich_tenant called with patched filing."""
    filing = _filing(tenant_name="BRETT LILLY")
    resolved = "123 Elm St, Cincinnati, OH 45202"

    mock_enriched = EnrichedContact(
        filing=filing, track="ng", phone="5550001111", dnc_status="clear", dnc_source="batchdata"
    )

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock,
               return_value=(None, resolved)), \
         patch("services.batchdata_service.enrich_tenant", new_callable=AsyncMock,
               return_value=mock_enriched) as mock_enrich:
        result = await batchdata_service.enrich_tenant_by_name(filing)

    mock_enrich.assert_called_once()
    patched_filing = mock_enrich.call_args[0][0]
    assert patched_filing.property_address == resolved
    assert result.phone == "5550001111"


@pytest.mark.asyncio
async def test_multi_tenant_tries_both_names(mock_cache):
    """4-token name split → second person match returned if first misses."""
    filing = _filing(tenant_name="AVONTE DUPREE ASHANTE LILLY")

    async def fake_searchbug(first, last, city, state, postal=""):
        if first.lower() == "avonte":
            return None, None
        if first.lower() == "ashante":
            return "5554445555", None
        return None, None

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("services.searchbug_service.search_tenant", side_effect=fake_searchbug):
        result = await batchdata_service.enrich_tenant_by_name(filing)

    assert result.phone == "5554445555"


@pytest.mark.asyncio
async def test_daily_cap_exceeded_skips_call(mock_cache, monkeypatch):
    """When daily cap is 0, SearchBug never called."""
    monkeypatch.setenv("SEARCHBUG_DAILY_CAP", "0")
    filing = _filing(tenant_name="BRETT LILLY")

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock) as mock_sb:
        result = await batchdata_service.enrich_tenant_by_name(filing)

    mock_sb.assert_not_called()
    assert result.phone is None


@pytest.mark.asyncio
async def test_zip_resolved_from_city(mock_cache):
    """Cincinnati OH → ZIP 45202 appended to SearchBug call."""
    filing = _filing(tenant_name="BRETT LILLY", property_address="Cincinnati, OH")

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock,
               return_value=(None, None)) as mock_sb:
        await batchdata_service.enrich_tenant_by_name(filing)

    call_kwargs = mock_sb.call_args
    assert call_kwargs.kwargs.get("postal") == "45202" or \
           (call_kwargs.args and "45202" in call_kwargs.args)


@pytest.mark.asyncio
async def test_middle_initial_parsed_correctly(mock_cache):
    """'BRETT L LILLY' → parse_name strips L → SearchBug gets first='BRETT' last='LILLY'."""
    filing = _filing(tenant_name="BRETT L LILLY")

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock,
               return_value=("5550009999", None)) as mock_sb:
        result = await batchdata_service.enrich_tenant_by_name(filing)

    first, last = mock_sb.call_args.args[:2]
    assert first.lower() == "brett"
    assert last.lower() == "lilly"
    assert result.phone == "5550009999"
