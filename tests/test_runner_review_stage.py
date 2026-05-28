"""Review-stage routing: NG leads with name_mismatch or ambiguous searchbug_status
are pushed to GHL_NG_REVIEW_STAGE_ID with a review tag, skipping Instantly and Bland."""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from models.contact import EnrichedContact
from models.filing import Filing
from pipeline.runner import _process_track, TrackResult


def _filing(**kw) -> Filing:
    values = dict(
        case_number="TEST-REVIEW-001",
        tenant_name="Maria Garcia",
        property_address="123 Main St, Nashville, TN 37211",
        landlord_name="Bob Smith",
        filing_date=date.today(),
        state="TN",
        county="Davidson",
        notice_type="Eviction",
        source_url="https://example.test",
        property_type_hint="residential",
    )
    values.update(kw)
    return Filing(**values)


def _ng_contact(filing, searchbug_status: str | None = "phone_found", **kw):
    return EnrichedContact(
        filing=filing,
        track="ng",
        phone="6152222222",
        property_type="residential",
        searchbug_status=searchbug_status,
        **kw,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected_tag", [
    ("name_mismatch", "Review-NameMismatch"),
    ("ambiguous", "Review-Ambiguous"),
])
async def test_review_status_routes_to_review_stage(status, expected_tag):
    """name_mismatch/ambiguous NG leads go to GHL_NG_REVIEW_STAGE_ID, not the normal stage."""
    filing = _filing()
    contact = _ng_contact(filing, searchbug_status=status)

    with patch("pipeline.runner.GHL_NG_REVIEW_STAGE_ID", "review-stage-id"), \
         patch("pipeline.runner.GHL_NG_RESIDENTIAL_STAGE_ID", "normal-stage-id"), \
         patch("services.ghl_service.create_contact", new_callable=AsyncMock,
               return_value="ghl-review-001") as mock_ghl, \
         patch("services.dedup_service.update_ghl_id", new_callable=AsyncMock), \
         patch("services.instantly_service.enroll", new_callable=AsyncMock) as mock_instantly, \
         patch("services.bland_service.trigger_voicemail", new_callable=AsyncMock) as mock_bland, \
         patch("services.dedup_service.set_bland_status", new_callable=AsyncMock) as mock_bland_status:
        result = await _process_track(contact)

    assert result.ghl_created is True
    assert result.is_review is True
    assert result.instantly_enrolled is False

    # GHL called with review stage, not normal stage
    mock_ghl.assert_called_once()
    call_args = mock_ghl.call_args
    assert call_args[0][2] == "review-stage-id"   # stage_id arg
    assert expected_tag in call_args[0][1]          # tag in tags list

    # Instantly and Bland never called
    mock_instantly.assert_not_called()
    mock_bland.assert_not_called()
    mock_bland_status.assert_not_called()


@pytest.mark.asyncio
async def test_review_status_no_review_stage_id_returns_false():
    """When GHL_NG_REVIEW_STAGE_ID is unset, review lead is dropped (no push)."""
    filing = _filing()
    contact = _ng_contact(filing, searchbug_status="name_mismatch")

    with patch("pipeline.runner.GHL_NG_REVIEW_STAGE_ID", ""), \
         patch("services.ghl_service.create_contact", new_callable=AsyncMock) as mock_ghl:
        result = await _process_track(contact)

    assert result.ghl_created is False
    assert result.is_review is True
    mock_ghl.assert_not_called()


@pytest.mark.asyncio
async def test_review_status_no_phone_returns_false():
    """Review lead without phone is not pushed to GHL."""
    filing = _filing()
    contact = EnrichedContact(
        filing=filing, track="ng", phone=None,
        searchbug_status="name_mismatch",
    )

    with patch("services.ghl_service.create_contact", new_callable=AsyncMock) as mock_ghl:
        result = await _process_track(contact)

    # No phone → caught by _has_contact_method guard before review check
    assert result.ghl_created is False
    mock_ghl.assert_not_called()


@pytest.mark.asyncio
async def test_phone_found_status_uses_normal_stage():
    """phone_found status follows the normal NG routing path, not review."""
    filing = _filing()
    contact = _ng_contact(filing, searchbug_status="phone_found")

    with patch("pipeline.runner.GHL_NG_REVIEW_STAGE_ID", "review-stage-id"), \
         patch("pipeline.runner.GHL_NG_RESIDENTIAL_STAGE_ID", "normal-stage-id"), \
         patch("services.ghl_service.create_contact", new_callable=AsyncMock,
               return_value="ghl-normal-001") as mock_ghl, \
         patch("services.dedup_service.update_ghl_id", new_callable=AsyncMock), \
         patch("services.instantly_service.enroll", new_callable=AsyncMock,
               return_value=type("R", (), {"enrolled": True, "error": None})()) as mock_instantly, \
         patch("services.dedup_service.set_bland_status", new_callable=AsyncMock):
        result = await _process_track(contact)

    assert result.is_review is False
    mock_ghl.assert_called_once()
    # Called with the normal stage, not the review stage
    assert mock_ghl.call_args[0][2] == "normal-stage-id"
    mock_instantly.assert_called_once()
