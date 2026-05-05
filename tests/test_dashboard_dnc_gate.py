from __future__ import annotations

import pytest
from fastapi import HTTPException

from dashboard import main as dashboard_main


@pytest.mark.asyncio
async def test_dashboard_approve_blocks_dnc_before_bland(monkeypatch):
    async def pending(*args, **kwargs):
        return [
            {
                "case_number": "TEST-DASH-DNC",
                "tenant_name": "Jane Tenant",
                "landlord_name": "Grant Owner",
                "property_address": "123 Main St, Los Angeles, CA 90001",
                "state": "CA",
                "county": "Los Angeles",
                "filing_date": "2026-05-06",
                "court_date": None,
                "phone": "+12135550100",
                "email": "owner@example.test",
                "property_type": "residential",
                "dnc_status": "blocked",
                "dnc_source": "test",
            }
        ]

    async def fail_if_called(contact):
        raise AssertionError("Bland should not fire from dashboard when DNC is blocked")

    statuses: list[tuple[str, str, str]] = []

    async def capture_status(case_number: str, track: str, status: str, call_id: str | None = None):
        statuses.append((case_number, track, status))

    monkeypatch.setattr(dashboard_main, "get_pending_leads", pending)
    monkeypatch.setattr(dashboard_main.bland_service, "trigger_voicemail", fail_if_called)
    monkeypatch.setattr(dashboard_main, "set_bland_status", capture_status)

    with pytest.raises(HTTPException) as exc:
        await dashboard_main.approve("TEST-DASH-DNC")

    assert exc.value.status_code == 400
    assert "DNC" in str(exc.value.detail)
    assert statuses == [("TEST-DASH-DNC", "ec", "blocked_dnc")]
