from __future__ import annotations

from datetime import date

import pytest

from models.contact import EnrichedContact
from models.filing import Filing
from pipeline import runner


def _contact(dnc_status: str) -> EnrichedContact:
    filing = Filing(
        case_number=f"TEST-DNC-{dnc_status}",
        tenant_name="Jane Tenant",
        property_address="123 Main St, Los Angeles, CA 90001",
        landlord_name="Grant Owner",
        filing_date=date(2026, 5, 6),
        state="CA",
        county="Los Angeles",
        notice_type="Unlawful Detainer",
        source_url="https://example.test",
    )
    return EnrichedContact(
        filing=filing,
        track="ec",
        phone="+12135550100",
        estimated_rent=2000,
        property_type="residential",
        dnc_status=dnc_status,
        dnc_source="test",
    )


def _ng_spanish_contact() -> EnrichedContact:
    filing = Filing(
        case_number="TEST-SPANISH-TAG",
        tenant_name="Maria Garcia",
        property_address="123 Main St, Houston, TX 77002",
        landlord_name="Grant Owner",
        filing_date=date(2026, 5, 6),
        state="TX",
        county="Harris",
        notice_type="Eviction",
        source_url="https://example.test",
    )
    return EnrichedContact(
        filing=filing,
        track="ng",
        phone="+12135550100",
        estimated_rent=1800,
        property_type="residential",
        dnc_status="clear",
        dnc_source="test",
        language_hint="spanish_likely",
    )


@pytest.mark.asyncio
async def test_process_track_blocks_bland_when_dnc_not_clear(monkeypatch):
    monkeypatch.setattr(runner, "_AUTO_BLAND_CALLS_ENABLED", True)
    monkeypatch.setattr(runner.ghl_service, "create_contact", _async_return("ghl-123"))
    monkeypatch.setattr(runner.dedup_service, "update_ghl_id", _async_none)

    statuses: list[tuple[str, str, str | None]] = []

    async def capture_status(case_number: str, track: str, status: str, call_id: str | None = None):
        statuses.append((case_number, track, status))

    async def fail_if_called(contact: EnrichedContact) -> str:
        raise AssertionError("Bland should not fire when DNC is not clear")

    monkeypatch.setattr(runner.dedup_service, "set_bland_status", capture_status)
    monkeypatch.setattr(runner.bland_service, "trigger_voicemail", fail_if_called)

    created = await runner._process_track(_contact("blocked"))

    assert created is True
    assert statuses == [("TEST-DNC-blocked", "ec", "blocked_dnc")]


@pytest.mark.asyncio
async def test_process_track_triggers_bland_when_dnc_clear(monkeypatch):
    monkeypatch.setattr(runner, "_AUTO_BLAND_CALLS_ENABLED", True)
    monkeypatch.setattr(runner.ghl_service, "create_contact", _async_return("ghl-123"))
    monkeypatch.setattr(runner.dedup_service, "update_ghl_id", _async_none)

    statuses: list[tuple[str, str, str, str | None]] = []

    async def capture_status(case_number: str, track: str, status: str, call_id: str | None = None):
        statuses.append((case_number, track, status, call_id))

    monkeypatch.setattr(runner.dedup_service, "set_bland_status", capture_status)
    monkeypatch.setattr(runner.bland_service, "trigger_voicemail", _async_return("call-123"))

    created = await runner._process_track(_contact("clear"))

    assert created is True
    assert statuses == [("TEST-DNC-clear", "ec", "triggered", "call-123")]


@pytest.mark.asyncio
async def test_process_track_adds_spanish_likely_tag_to_ng_contacts(monkeypatch):
    captured_tags: list[str] = []

    async def create_contact(contact: EnrichedContact, tags: list[str], pipeline_stage_id: str):
        captured_tags.extend(tags)
        return "ghl-123"

    monkeypatch.setattr(runner, "_AUTO_BLAND_CALLS_ENABLED", False)
    monkeypatch.setattr(runner.ghl_service, "create_contact", create_contact)
    monkeypatch.setattr(runner.dedup_service, "update_ghl_id", _async_none)
    monkeypatch.setattr(runner.dedup_service, "set_bland_status", _async_none)

    created = await runner._process_track(_ng_spanish_contact())

    assert created is True
    assert "NG-New-Filing" in captured_tags
    assert "Spanish-Likely" in captured_tags


@pytest.mark.asyncio
async def test_process_track_uses_ec_stage_for_ec_commercial_contacts(monkeypatch):
    captured: list[tuple[list[str], str]] = []
    contact = _contact("clear")
    contact.property_type = "commercial"

    async def create_contact(contact: EnrichedContact, tags: list[str], pipeline_stage_id: str):
        captured.append((tags, pipeline_stage_id))
        return "ghl-123"

    monkeypatch.setattr(runner, "GHL_EC_STAGE_ID", "ec-stage")
    monkeypatch.setattr(runner, "GHL_NG_COMMERCIAL_STAGE_ID", "ng-commercial-stage")
    monkeypatch.setattr(runner, "_AUTO_BLAND_CALLS_ENABLED", False)
    monkeypatch.setattr(runner.ghl_service, "create_contact", create_contact)
    monkeypatch.setattr(runner.dedup_service, "update_ghl_id", _async_none)
    monkeypatch.setattr(runner.dedup_service, "set_bland_status", _async_none)

    created = await runner._process_track(contact)

    assert created is True
    assert captured == [(["Commercial", "High-Priority"], "ec-stage")]


def _async_return(value):
    async def inner(*args, **kwargs):
        return value

    return inner


async def _async_none(*args, **kwargs):
    return None
