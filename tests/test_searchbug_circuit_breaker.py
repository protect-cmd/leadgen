"""Process-level circuit breaker for SearchBug account errors.

When SearchBug returns a billing error we don't want to keep hitting the API
for the rest of the process — every call burns the daily-cap counter and adds
HTTP latency without producing results. Once tripped, the breaker stays
tripped until process restart (next Railway deploy after credit top-up).
"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from models.filing import Filing
from services import batchdata_service, searchbug_service
from services.enrichment_cache import EnrichmentCache


@pytest.fixture(autouse=True)
def reset_breaker():
    searchbug_service.reset_circuit_breaker_for_tests()
    yield
    searchbug_service.reset_circuit_breaker_for_tests()


@pytest.fixture
def mock_cache(tmp_path):
    return EnrichmentCache(db_path=str(tmp_path / "test.db"))


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("SEARCHBUG_CO_CODE", "test-co")
    monkeypatch.setenv("SEARCHBUG_API_KEY", "test-key")
    monkeypatch.setenv("BATCHDATA_API_KEY", "test-key")
    monkeypatch.setenv("PUSHOVER_ENABLED", "false")  # silence Pushover in tests


def _account_error_response(*args, **kwargs):
    return httpx.Response(
        200,
        json={
            "Status": "Error",
            "Error": "Your prepaid plan balance is required. Error Code: 12730510--0.10985-25-",
        },
    )


@pytest.mark.asyncio
async def test_first_account_error_trips_breaker():
    assert not searchbug_service.is_account_error_tripped()

    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=_account_error_response)):
        result = await searchbug_service.search_tenant_detailed("A", "B", "C", "D")

    assert result.status == "account_error"
    assert searchbug_service.is_account_error_tripped()


@pytest.mark.asyncio
async def test_subsequent_calls_short_circuit_without_http():
    # Prime the breaker
    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=_account_error_response)):
        await searchbug_service.search_tenant_detailed("A", "B", "C", "D")
    assert searchbug_service.is_account_error_tripped()

    # Subsequent call should NOT hit the wire
    with patch("httpx.AsyncClient.post", new=AsyncMock()) as mock_post:
        result = await searchbug_service.search_tenant_detailed("E", "F", "G", "H")

    mock_post.assert_not_called()
    assert result.status == "account_error"
    assert result.retryable is False


@pytest.mark.asyncio
async def test_green_path_skips_searchbug_after_breaker_trips(mock_cache, monkeypatch):
    monkeypatch.setenv("SEARCHBUG_DAILY_CAP", "100")
    searchbug_service._account_error_tripped = True  # pretend earlier call tripped

    filing = Filing(
        case_number="CB-001",
        tenant_name="Brett Lilly",
        property_address="1 Main St, Cincinnati, OH 45202",
        landlord_name="LL",
        filing_date=date(2026, 5, 22),
        state="OH",
        county="Hamilton",
        notice_type="X",
        source_url="x",
    )

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock) as mock_sb:
        result = await batchdata_service._searchbug_fallback_gated(
            filing, filing.tenant_name
        )

    mock_sb.assert_not_called()
    assert result is None
    # Most important: daily cap counter MUST NOT have been incremented
    assert mock_cache.check_daily_cap(100) is True
    row_count = 0
    import sqlite3
    with sqlite3.connect(mock_cache._db_path) as con:
        row = con.execute(
            "SELECT count FROM daily_cap WHERE date=?",
            (date.today().isoformat(),),
        ).fetchone()
        row_count = row[0] if row else 0
    assert row_count == 0, f"Daily cap should not have been incremented; got {row_count}"

