"""Regression: post-DNC-removal, any phone contact reaches GHL + Instantly
without DNC gating. There is no DNC gate any more."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date
from models.filing import Filing
from models.contact import EnrichedContact
from pipeline import runner


@pytest.mark.asyncio
async def test_phone_contact_proceeds_to_ghl_and_instantly_without_dnc(monkeypatch):
    monkeypatch.setattr(runner, "_CAPTURE_EXPANDED_ZIPS", False)
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "true")
    monkeypatch.setenv("LANDLORD_TRACK_ENABLED", "false")
    monkeypatch.setenv("GHL_NG_NEW_FILING_STAGE_ID", "stage_id_xyz")

    filing = Filing(
        case_number="NODNC1", tenant_name="Maria Garcia",
        property_address="123 Main St, Houston, TX 77002",
        landlord_name="ACME", filing_date=date.today(),
        state="TX", county="Harris",
        notice_type="Nonpayment - Residential", source_url="x",
    )
    ng = EnrichedContact(
        filing=filing, track="ng", phone="5551234567", email=None,
        property_type="residential",
    )

    with patch("services.batchdata_service.enrich_tenant", new=AsyncMock(return_value=ng)), \
         patch("services.ghl_service.create_contact", new=AsyncMock(return_value="ghl_123")) as mock_ghl, \
         patch("services.instantly_service.enroll", new=AsyncMock(return_value=MagicMock(enrolled=True, error=None))) as mock_instantly, \
         patch("services.dedup_service.is_duplicate", new=AsyncMock(return_value=False)), \
         patch("services.dedup_service.insert_filing", new=AsyncMock()), \
         patch("services.dedup_service.update_classification", new=AsyncMock()), \
         patch("services.dedup_service.update_enrichment", new=AsyncMock()), \
         patch("services.dedup_service.update_ghl_id", new=AsyncMock()), \
         patch("services.dedup_service.has_ng_phone", new=AsyncMock(return_value=False)), \
         patch("services.dedup_service.set_bland_status", new=AsyncMock()), \
         patch("services.geocode_service.normalize_address", new=AsyncMock(return_value=None)), \
         patch("services.notification_service.send_run_summary", new=AsyncMock()), \
         patch("services.dedup_service.write_run_metrics", new=AsyncMock()):

        await runner.run([filing], state="TX", county="Harris")

    mock_ghl.assert_called_once()
    mock_instantly.assert_called_once()
