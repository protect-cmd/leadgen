from __future__ import annotations

import contextlib
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.contact import EnrichedContact
from models.filing import Filing


def _filing(**kwargs) -> Filing:
    values = dict(
        case_number="TEST-001",
        tenant_name="Jane Doe",
        property_address="123 Main St, Nashville, TN 37211",
        landlord_name="Bob Smith",
        filing_date=date(2026, 5, 16),
        state="TN",
        county="Davidson",
        notice_type="Eviction",
        source_url="https://example.test",
        property_type_hint="residential",
    )
    values.update(kwargs)
    return Filing(**values)


def _ec_contact(filing):
    return EnrichedContact(
        filing=filing, track="ec", phone="6151111111",
        dnc_status="clear", dnc_source="batchdata",
        property_type="residential", estimated_rent=1800,
    )


def _ng_contact(filing):
    return EnrichedContact(
        filing=filing, track="ng", phone="6152222222",
        dnc_status="clear", dnc_source="batchdata",
        property_type="residential", estimated_rent=1800,
    )


def _base_patches(filing, ec_ret=None, ng_ret=None):
    """Common service patches letting a filing reach enrichment."""
    return [
        patch("services.dedup_service.is_duplicate", new_callable=AsyncMock, return_value=False),
        patch("services.dedup_service.insert_filing", new_callable=AsyncMock),
        patch("services.dedup_service.update_language_hint", new_callable=AsyncMock),
        patch("services.dedup_service.update_enrichment", new_callable=AsyncMock),
        patch("services.dedup_service.update_classification", new_callable=AsyncMock),
        patch("services.dedup_service.update_ghl_id", new_callable=AsyncMock),
        patch("services.dedup_service.set_bland_status", new_callable=AsyncMock),
        patch("services.dedup_service.write_run_metrics", new_callable=AsyncMock),
        patch("services.geocode_service.normalize_address", new_callable=AsyncMock, return_value=None),
        patch("services.language_service.language_hint_for_name", return_value=None),
        patch("services.notification_service.send_run_summary", new_callable=AsyncMock),
        patch("pipeline.runner._process_track", new_callable=AsyncMock,
              return_value=MagicMock(ghl_created=True, instantly_enrolled=False, instantly_error=None)),
        patch("services.batchdata_service.enrich", new_callable=AsyncMock,
              return_value=ec_ret or _ec_contact(filing)),
        patch("services.batchdata_service.enrich_tenant", new_callable=AsyncMock,
              return_value=ng_ret or _ng_contact(filing)),
    ]


@pytest.fixture(autouse=True)
def ghl_stage_ids(monkeypatch):
    monkeypatch.setenv("GHL_NEW_FILING_STAGE_ID", "stage-ec")
    monkeypatch.setenv("GHL_NG_NEW_FILING_STAGE_ID", "stage-ng")


@pytest.mark.asyncio
async def test_tenant_only_mode_calls_enrich_tenant_not_enrich(monkeypatch):
    """TENANT=true, LANDLORD=false → enrich() never called."""
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "true")
    monkeypatch.setenv("LANDLORD_TRACK_ENABLED", "false")
    filing = _filing()

    with contextlib.ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in _base_patches(filing)]
        mock_enrich = mocks[-2]
        mock_enrich_tenant = mocks[-1]
        from pipeline import runner
        await runner.run([filing], state="TN", county="Davidson")

    mock_enrich.assert_not_called()
    mock_enrich_tenant.assert_called_once()


@pytest.mark.asyncio
async def test_landlord_only_mode_calls_enrich_not_enrich_tenant(monkeypatch):
    """TENANT=false, LANDLORD=true → enrich_tenant() never called."""
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "false")
    monkeypatch.setenv("LANDLORD_TRACK_ENABLED", "true")
    filing = _filing()

    with contextlib.ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in _base_patches(filing)]
        mock_enrich = mocks[-2]
        mock_enrich_tenant = mocks[-1]
        from pipeline import runner
        await runner.run([filing], state="TN", county="Davidson")

    mock_enrich.assert_called_once()
    mock_enrich_tenant.assert_not_called()


@pytest.mark.asyncio
async def test_dual_track_mode_calls_both(monkeypatch):
    """TENANT=true, LANDLORD=true → both enrich() and enrich_tenant() called."""
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "true")
    monkeypatch.setenv("LANDLORD_TRACK_ENABLED", "true")
    filing = _filing()

    with contextlib.ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in _base_patches(filing)]
        mock_enrich = mocks[-2]
        mock_enrich_tenant = mocks[-1]
        from pipeline import runner
        await runner.run([filing], state="TN", county="Davidson")

    mock_enrich.assert_called_once()
    mock_enrich_tenant.assert_called_once()


@pytest.mark.asyncio
async def test_both_tracks_disabled_raises_runtime_error(monkeypatch):
    """TENANT=false, LANDLORD=false → RuntimeError raised."""
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "false")
    monkeypatch.setenv("LANDLORD_TRACK_ENABLED", "false")

    from pipeline import runner
    with pytest.raises(RuntimeError, match="TENANT_TRACK_ENABLED|both.*disabled|Invalid config"):
        await runner.run([_filing()], state="TN", county="Davidson")


@pytest.mark.asyncio
async def test_default_config_is_tenant_only(monkeypatch):
    """No track env vars → defaults to tenant-only."""
    monkeypatch.delenv("TENANT_TRACK_ENABLED", raising=False)
    monkeypatch.delenv("LANDLORD_TRACK_ENABLED", raising=False)
    filing = _filing()

    with contextlib.ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in _base_patches(filing)]
        mock_enrich = mocks[-2]
        mock_enrich_tenant = mocks[-1]
        from pipeline import runner
        await runner.run([filing], state="TN", county="Davidson")

    mock_enrich.assert_not_called()
    mock_enrich_tenant.assert_called_once()
