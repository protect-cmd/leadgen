from __future__ import annotations

import pytest
from fastapi import HTTPException

from dashboard import main as dashboard_main


@pytest.mark.asyncio
async def test_bland_test_call_blocks_when_disabled(monkeypatch):
    monkeypatch.setenv("BLAND_ENABLED", "true")
    monkeypatch.setenv("BLAND_TEST_CALLS_ENABLED", "false")

    with pytest.raises(HTTPException) as exc:
        await dashboard_main.bland_test_call("ec")

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_bland_test_call_triggers_ec_internal_recipient(monkeypatch):
    contacts = []

    async def trigger_voicemail(contact):
        contacts.append(contact)
        return "call-ec"

    monkeypatch.setenv("BLAND_ENABLED", "true")
    monkeypatch.setenv("BLAND_TEST_CALLS_ENABLED", "true")
    monkeypatch.setattr(dashboard_main.bland_service, "trigger_voicemail", trigger_voicemail)

    result = await dashboard_main.bland_test_call("ec")

    assert result == {"status": "triggered", "track": "ec", "call_id": "call-ec"}
    assert contacts[0].track == "ec"
    assert contacts[0].phone == dashboard_main._BLAND_TEST_RECIPIENTS["ec"]
    assert contacts[0].dnc_status == "clear"


@pytest.mark.asyncio
async def test_bland_test_call_triggers_spanish_internal_recipient(monkeypatch):
    contacts = []

    async def trigger_voicemail(contact):
        contacts.append(contact)
        return "call-ng-es"

    monkeypatch.setenv("BLAND_ENABLED", "true")
    monkeypatch.setenv("BLAND_TEST_CALLS_ENABLED", "true")
    monkeypatch.setattr(dashboard_main.bland_service, "trigger_voicemail", trigger_voicemail)

    result = await dashboard_main.bland_test_call("ng_spanish")

    assert result == {"status": "triggered", "track": "ng_spanish", "call_id": "call-ng-es"}
    assert contacts[0].track == "ng"
    assert contacts[0].phone == dashboard_main._BLAND_TEST_RECIPIENTS["ng_spanish"]
    assert contacts[0].language_hint == "spanish_likely"


@pytest.mark.asyncio
async def test_bland_test_call_rejects_unknown_track(monkeypatch):
    monkeypatch.setenv("BLAND_ENABLED", "true")
    monkeypatch.setenv("BLAND_TEST_CALLS_ENABLED", "true")

    with pytest.raises(HTTPException) as exc:
        await dashboard_main.bland_test_call("bad")

    assert exc.value.status_code == 404
