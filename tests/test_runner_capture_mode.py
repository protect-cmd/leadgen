"""Capture mode: off-allowlist filings land in lead_bucket='captured' and
skip enrichment + routing + GHL + Bland + Instantly entirely."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, patch
from datetime import date
from models.filing import Filing
from pipeline import runner


@pytest.mark.asyncio
async def test_capture_mode_short_circuits_enrichment(monkeypatch):
    monkeypatch.setattr(runner, "_CAPTURE_EXPANDED_ZIPS", True)
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "true")
    monkeypatch.setenv("LANDLORD_TRACK_ENABLED", "false")

    # 77090 (Greenspoint) is off the TX allowlist.
    filing = Filing(
        case_number="CAP1", tenant_name="Maria Garcia",
        property_address="123 Greenspoint Dr, Houston, TX 77090",
        landlord_name="ACME LLC", filing_date=date(2026, 5, 25),
        state="TX", county="Harris",
        notice_type="Nonpayment - Residential", source_url="x",
    )

    with patch("services.batchdata_service.lookup_property_info", new=AsyncMock()) as mock_lookup, \
         patch("services.batchdata_service.enrich_tenant", new=AsyncMock()) as mock_tenant, \
         patch("services.ghl_service.create_contact", new=AsyncMock()) as mock_ghl, \
         patch("services.instantly_service.enroll", new=AsyncMock()) as mock_instantly, \
         patch("services.bland_service.trigger_voicemail", new=AsyncMock()) as mock_bland, \
         patch("services.dedup_service.is_duplicate", new=AsyncMock(return_value=False)), \
         patch("services.dedup_service.insert_filing", new=AsyncMock()), \
         patch("services.dedup_service.update_classification", new=AsyncMock()), \
         patch("services.geocode_service.normalize_address", new=AsyncMock(return_value=None)), \
         patch("services.notification_service.send_run_summary", new=AsyncMock()), \
         patch("services.dedup_service.write_run_metrics", new=AsyncMock()):

        await runner.run([filing], state="TX", county="Harris")

    mock_lookup.assert_not_called()
    mock_tenant.assert_not_called()
    mock_ghl.assert_not_called()
    mock_instantly.assert_not_called()
    mock_bland.assert_not_called()


@pytest.mark.asyncio
async def test_capture_mode_off_allowlist_zip_discarded_when_flag_off(monkeypatch):
    """Regression: when CAPTURE_EXPANDED_ZIPS=False, off-allowlist still discards."""
    monkeypatch.setattr(runner, "_CAPTURE_EXPANDED_ZIPS", False)
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "true")
    monkeypatch.setenv("LANDLORD_TRACK_ENABLED", "false")

    filing = Filing(
        case_number="LEG1", tenant_name="Maria Garcia",
        property_address="123 Greenspoint Dr, Houston, TX 77090",
        landlord_name="ACME LLC", filing_date=date(2026, 5, 25),
        state="TX", county="Harris",
        notice_type="Nonpayment - Residential", source_url="x",
    )

    with patch("services.batchdata_service.enrich_tenant", new=AsyncMock()) as mock_tenant, \
         patch("services.dedup_service.is_duplicate", new=AsyncMock(return_value=False)), \
         patch("services.dedup_service.insert_filing", new=AsyncMock()), \
         patch("services.dedup_service.update_classification", new=AsyncMock()), \
         patch("services.geocode_service.normalize_address", new=AsyncMock(return_value=None)), \
         patch("services.notification_service.send_run_summary", new=AsyncMock()), \
         patch("services.dedup_service.write_run_metrics", new=AsyncMock()):

        await runner.run([filing], state="TX", county="Harris")

    # Off-allowlist with flag OFF → still skipped, but as legacy discard not captured.
    mock_tenant.assert_not_called()
