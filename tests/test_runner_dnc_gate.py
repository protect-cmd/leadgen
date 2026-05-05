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


def _async_return(value):
    async def inner(*args, **kwargs):
        return value

    return inner


async def _async_none(*args, **kwargs):
    return None
