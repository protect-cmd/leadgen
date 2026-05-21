"""Pushover alert wiring for tonight's new failure modes.

Covers:
1. EnrichmentCache.claim_alert_once_today — once-per-day dedupe primitive.
2. SearchBug account_error fires a Pushover alert (high priority).
3. SearchBug daily-cap hit fires a Pushover alert (deduped per day).
4. FTC DNC registry load failure fires a Pushover alert at startup.
5. send_run_summary surfaces NG / SearchBug / FTC counters when present.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from models.filing import Filing
from services import batchdata_service, notification_service
from services.enrichment_cache import EnrichmentCache


def _filing(**kwargs) -> Filing:
    values = {
        "case_number": "PUSH-001",
        "tenant_name": "Brett Lilly",
        "property_address": "123 Main St, Cincinnati, OH 45202",
        "landlord_name": "LL",
        "filing_date": date(2026, 5, 22),
        "state": "OH",
        "county": "Hamilton",
        "notice_type": "X",
        "source_url": "x",
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
    monkeypatch.setenv("PUSHOVER_ENABLED", "true")
    monkeypatch.setenv("PUSHOVER_APP_TOKEN", "test-token")
    monkeypatch.setenv("PUSHOVER_USER_KEY", "test-user")


def test_claim_alert_once_today_dedupes(mock_cache):
    assert mock_cache.claim_alert_once_today("searchbug_daily_cap") is True
    assert mock_cache.claim_alert_once_today("searchbug_daily_cap") is False
    # Different key still claimable
    assert mock_cache.claim_alert_once_today("searchbug_account_error") is True


@pytest.mark.asyncio
async def test_searchbug_account_error_fires_alert(mock_cache):
    from services import searchbug_service

    def _err_response(*args, **kwargs):
        return httpx.Response(
            200,
            json={
                "Status": "Error",
                "Error": "Your prepaid plan balance is required to perform this lookup. Error Code: 999",
            },
        )

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=_err_response)), \
         patch.object(notification_service, "send_alert", new=AsyncMock()) as mock_alert:
        result = await searchbug_service.search_tenant_detailed(
            "Brett", "Lilly", "Cincinnati", "OH"
        )

    assert result.status == "account_error"
    mock_alert.assert_awaited_once()
    title = mock_alert.call_args.args[0]
    assert "SearchBug account error" in title
    assert mock_alert.call_args.kwargs.get("priority") == 1


@pytest.mark.asyncio
async def test_searchbug_account_error_alert_deduped_per_day(mock_cache):
    from services import searchbug_service

    def _err_response(*args, **kwargs):
        return httpx.Response(
            200,
            json={"Status": "Error", "Error": "Your prepaid plan balance is required."},
        )

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=_err_response)), \
         patch.object(notification_service, "send_alert", new=AsyncMock()) as mock_alert:
        await searchbug_service.search_tenant_detailed("A", "B", "C", "D")
        await searchbug_service.search_tenant_detailed("E", "F", "G", "H")
        await searchbug_service.search_tenant_detailed("I", "J", "K", "L")

    # Only the first should have sent an alert
    assert mock_alert.await_count == 1


@pytest.mark.asyncio
async def test_daily_cap_hit_fires_alert(mock_cache, monkeypatch):
    monkeypatch.setenv("SEARCHBUG_DAILY_CAP", "1")
    filing = _filing(tenant_name="Brett Lilly")
    mock_cache.increment_daily_count()  # cap is 1, count is 1 → cap reached

    async def fake_post(*args, **kwargs):
        return httpx.Response(200, json={"results": {"persons": []}})

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=fake_post)), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock) as mock_sb, \
         patch.object(notification_service, "send_alert", new=AsyncMock()) as mock_alert:
        await batchdata_service.enrich_tenant(
            filing, lookup_property_if_missing=False
        )

    mock_sb.assert_not_called()
    mock_alert.assert_awaited_once()
    assert "daily cap" in mock_alert.call_args.args[0].lower()


@pytest.mark.asyncio
async def test_daily_cap_alert_deduped_per_day(mock_cache, monkeypatch):
    monkeypatch.setenv("SEARCHBUG_DAILY_CAP", "1")
    mock_cache.increment_daily_count()

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch.object(notification_service, "send_alert", new=AsyncMock()) as mock_alert:
        await batchdata_service._maybe_alert_cap_hit(1, source="green/OH/Hamilton")
        await batchdata_service._maybe_alert_cap_hit(1, source="green/OH/Franklin")
        await batchdata_service._maybe_alert_cap_hit(1, source="yellow/TX/Harris")

    assert mock_alert.await_count == 1


@pytest.mark.asyncio
async def test_ftc_registry_load_failure_fires_startup_alert(monkeypatch):
    monkeypatch.setenv("FTC_DNC_DB_PATH", "/nonexistent/dnc.db")
    monkeypatch.setenv("DASHBOARD_DAILY_SCHEDULER_ENABLED", "true")
    from services import dnc_service
    dnc_service.reset_registry_for_tests()

    from dashboard import main as dashboard_main

    with patch.object(notification_service, "send_alert", new=AsyncMock()) as mock_alert:
        await dashboard_main._preload_dnc_registry()

    mock_alert.assert_awaited_once()
    assert "FTC DNC" in mock_alert.call_args.args[0]
    assert mock_alert.call_args.kwargs.get("priority") == 1


@pytest.mark.asyncio
async def test_ftc_registry_unset_skips_alert(monkeypatch):
    monkeypatch.delenv("FTC_DNC_DB_PATH", raising=False)
    from dashboard import main as dashboard_main

    with patch.object(notification_service, "send_alert", new=AsyncMock()) as mock_alert:
        await dashboard_main._preload_dnc_registry()

    mock_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_run_summary_includes_new_counters():
    metrics = {
        "state": "OH",
        "county": "Hamilton",
        "filings_received": 50,
        "duplicates_skipped": 5,
        "address_skipped": 10,
        "batchdata_calls": 35,
        "phones_found": 20,
        "ghl_created": 15,
        "ng_phones_pushed": 12,
        "searchbug_calls": 30,
        "searchbug_daily_total": 45,
        "ftc_scrubs_upgraded": 8,
        "instantly_enrolled": 5,
        "elapsed_seconds": 42.0,
    }

    with patch.object(notification_service, "send_alert", new=AsyncMock()) as mock_alert:
        await notification_service.send_run_summary(metrics, auto_bland_enabled=False)

    mock_alert.assert_awaited_once()
    body = mock_alert.call_args.args[1]
    assert "NG (tenant) pushed: 12" in body
    assert "SearchBug calls: 30 (today: 45)" in body
    assert "FTC DNC upgrades: 8" in body
