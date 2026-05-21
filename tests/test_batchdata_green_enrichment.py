"""Green-source SearchBug fallback gate tests.

These cover `enrich_tenant`'s SearchBug fallback path (the function used by
green sources where BatchData has a property address but doesn't return a
matching tenant). The fallback must honor the same cost gates as the
yellow-source path: cache lookup, common-surname filter, and daily cap.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from models.filing import Filing
from services import batchdata_service
from services.enrichment_cache import EnrichmentCache


def _filing(**kwargs) -> Filing:
    values = {
        "case_number": "TEST-GREEN-001",
        "tenant_name": "Brett Lilly",
        "property_address": "123 Main St, Cincinnati, OH 45202",
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
def env(monkeypatch):
    monkeypatch.setenv("BATCHDATA_API_KEY", "test-key")
    monkeypatch.setenv("SEARCHBUG_CO_CODE", "test-co")
    monkeypatch.setenv("SEARCHBUG_API_KEY", "test-key")
    monkeypatch.setenv("SEARCHBUG_DAILY_CAP", "100")


def _empty_skip_trace_response():
    """BatchData skip-trace returns no persons — triggers SearchBug fallback."""
    return httpx.Response(200, json={"results": {"persons": []}})


@pytest.mark.asyncio
async def test_green_common_surname_skips_searchbug(mock_cache, monkeypatch):
    filing = _filing(tenant_name="John Smith")

    async def fake_post(*args, **kwargs):
        return _empty_skip_trace_response()

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=fake_post)), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock) as mock_sb:
        result = await batchdata_service.enrich_tenant(
            filing, lookup_property_if_missing=False
        )

    mock_sb.assert_not_called()
    assert result.phone is None


@pytest.mark.asyncio
async def test_green_cache_hit_skips_searchbug(mock_cache, monkeypatch):
    filing = _filing(tenant_name="Brett Lilly")
    mock_cache.set("brett", "lilly", "cincinnati", "oh", "5551112222", None)

    async def fake_post(*args, **kwargs):
        return _empty_skip_trace_response()

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=fake_post)), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock) as mock_sb:
        result = await batchdata_service.enrich_tenant(
            filing, lookup_property_if_missing=False
        )

    mock_sb.assert_not_called()
    assert result.phone == "5551112222"
    assert result.dnc_source == "searchbug"


@pytest.mark.asyncio
async def test_green_cached_miss_skips_searchbug(mock_cache, monkeypatch):
    filing = _filing(tenant_name="Brett Lilly")
    # Cached miss (None, None) means we already searched and got nothing.
    mock_cache.set("brett", "lilly", "cincinnati", "oh", None, None)

    async def fake_post(*args, **kwargs):
        return _empty_skip_trace_response()

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=fake_post)), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock) as mock_sb:
        result = await batchdata_service.enrich_tenant(
            filing, lookup_property_if_missing=False
        )

    mock_sb.assert_not_called()
    assert result.phone is None


@pytest.mark.asyncio
async def test_green_daily_cap_skips_searchbug(mock_cache, monkeypatch):
    monkeypatch.setenv("SEARCHBUG_DAILY_CAP", "1")
    filing = _filing(tenant_name="Brett Lilly")
    mock_cache.increment_daily_count()  # cap of 1 → already at limit

    async def fake_post(*args, **kwargs):
        return _empty_skip_trace_response()

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=fake_post)), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock) as mock_sb:
        result = await batchdata_service.enrich_tenant(
            filing, lookup_property_if_missing=False
        )

    mock_sb.assert_not_called()
    assert result.phone is None


@pytest.mark.asyncio
async def test_green_cache_miss_calls_searchbug_and_stores(mock_cache, monkeypatch):
    filing = _filing(tenant_name="Brett Lilly")

    async def fake_post(*args, **kwargs):
        return _empty_skip_trace_response()

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=fake_post)), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock,
               return_value=("5559998888", "456 Elm St, Cincinnati, OH 45202")) as mock_sb:
        result = await batchdata_service.enrich_tenant(
            filing, lookup_property_if_missing=False
        )

    mock_sb.assert_called_once()
    args, kwargs = mock_sb.call_args
    # search_tenant(first, last, city, state, postal)
    assert args[0] == "Brett"
    assert args[1] == "Lilly"
    assert args[2] == "Cincinnati"
    assert args[3] == "OH"
    assert args[4] == "45202"
    assert result.phone == "5559998888"
    assert result.dnc_source == "searchbug"

    # Stored in cache for next time
    cached = mock_cache.get("brett", "lilly", "cincinnati", "oh")
    assert cached == ("5559998888", "456 Elm St, Cincinnati, OH 45202")


@pytest.mark.asyncio
async def test_green_batchdata_phone_bypasses_searchbug(mock_cache, monkeypatch):
    """When BatchData returns a name-matched tenant with phone, SearchBug is not called."""
    filing = _filing(tenant_name="Brett Lilly")

    async def fake_post(*args, **kwargs):
        return httpx.Response(
            200,
            json={
                "results": {
                    "persons": [
                        {
                            "name": {"full": "Brett Lilly"},
                            "phoneNumbers": [
                                {"number": "5550000000", "type": "Mobile", "score": 90, "dnc": False}
                            ],
                            "emails": [],
                        }
                    ]
                }
            },
        )

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=fake_post)), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock) as mock_sb:
        result = await batchdata_service.enrich_tenant(
            filing, lookup_property_if_missing=False
        )

    mock_sb.assert_not_called()
    assert result.phone == "5550000000"
    assert result.dnc_status == "clear"
    assert result.dnc_source == "batchdata"
