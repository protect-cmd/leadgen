"""Phase 0 regression: in tenant-only mode, runner must not call
batchdata_service.lookup_property_info — property_type is inferred via heuristic."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, patch
from datetime import date
from models.filing import Filing
from models.contact import EnrichedContact
from pipeline import runner


@pytest.mark.asyncio
async def test_tenant_only_mode_does_not_call_lookup_property_info(monkeypatch):
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "true")
    monkeypatch.setenv("LANDLORD_TRACK_ENABLED", "false")

    filing = Filing(
        case_number="NB1", tenant_name="Maria Garcia",
        property_address="123 Oak St, Houston, TX 77002",
        landlord_name="ACME LLC", filing_date=date(2026, 5, 25),
        state="TX", county="Harris",
        notice_type="Nonpayment - Residential", source_url="x",
        property_type_hint=None,
    )
    ng_return = EnrichedContact(
        filing=filing, track="ng", phone=None, email=None,
        property_type="residential",
    )

    with patch("services.batchdata_service.lookup_property_info", new=AsyncMock()) as mock_lookup, \
         patch("services.batchdata_service.enrich_tenant", new=AsyncMock(return_value=ng_return)) as mock_tenant, \
         patch("services.dedup_service.is_duplicate", new=AsyncMock(return_value=False)), \
         patch("services.dedup_service.insert_filing", new=AsyncMock()), \
         patch("services.dedup_service.update_classification", new=AsyncMock()), \
         patch("services.dedup_service.update_enrichment", new=AsyncMock()), \
         patch("services.geocode_service.normalize_address", new=AsyncMock(return_value=None)), \
         patch("services.notification_service.send_run_summary", new=AsyncMock()), \
         patch("services.dedup_service.write_run_metrics", new=AsyncMock()):

        await runner.run([filing], state="TX", county="Harris")

    mock_lookup.assert_not_called()
    mock_tenant.assert_called_once()
