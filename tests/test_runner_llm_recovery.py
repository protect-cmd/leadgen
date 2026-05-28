"""Runner integration: LLM recovery branch fires only when LLM_RECOVERY_ENABLED
is true, only on regex-rejected leads, and only when the LLM returns a high-
confidence cleanup that re-passes the gate. Mutates filing in place so
downstream code sees the cleaned fields."""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from models.filing import Filing
from pipeline.runner import _maybe_llm_recover
from services.llm_recovery_service import RecoveryResult


def _filing(**kw) -> Filing:
    values = dict(
        case_number="LLM-001",
        tenant_name="Maria Garcia",
        property_address="123 Main St, Houston, TX 77002",
        landlord_name="LL",
        filing_date=date.today(),
        state="TX",
        county="Harris",
        notice_type="Eviction",
        source_url="https://example.test",
    )
    values.update(kw)
    return Filing(**values)


@pytest.mark.asyncio
async def test_disabled_short_circuits(monkeypatch):
    monkeypatch.setenv("LLM_RECOVERY_ENABLED", "false")
    filing = _filing(property_address="bad address")
    with patch("services.llm_recovery_service.recover", new_callable=AsyncMock) as mock_llm:
        recovered = await _maybe_llm_recover(filing, reason="address")
    assert recovered is False
    mock_llm.assert_not_called()


@pytest.mark.asyncio
async def test_low_confidence_keeps_rejection(monkeypatch):
    monkeypatch.setenv("LLM_RECOVERY_ENABLED", "true")
    filing = _filing(property_address="garbage")
    low_conf = RecoveryResult(
        street="1 Real St", city="Houston", state="TX", zip="77002",
        confidence=0.5,
    )
    with patch("services.llm_recovery_service.recover",
               new_callable=AsyncMock, return_value=low_conf):
        recovered = await _maybe_llm_recover(filing, reason="address")
    assert recovered is False
    assert filing.property_address == "garbage"  # not mutated


@pytest.mark.asyncio
async def test_skip_reason_keeps_rejection(monkeypatch):
    monkeypatch.setenv("LLM_RECOVERY_ENABLED", "true")
    filing = _filing(tenant_name="John Doe")
    result = RecoveryResult(
        first="John", last="Doe",
        confidence=0.95,
        skip_reason="Placeholder name (John Doe)",
    )
    with patch("services.llm_recovery_service.recover",
               new_callable=AsyncMock, return_value=result):
        recovered = await _maybe_llm_recover(filing, reason="name")
    assert recovered is False
    assert filing.tenant_name == "John Doe"


@pytest.mark.asyncio
async def test_high_confidence_address_recovery_mutates_filing(monkeypatch):
    monkeypatch.setenv("LLM_RECOVERY_ENABLED", "true")
    filing = _filing(property_address="123 Main, Houston TX")
    result = RecoveryResult(
        street="123 Main St", city="Houston", state="TX", zip="77002",
        confidence=0.92,
    )
    with patch("services.llm_recovery_service.recover",
               new_callable=AsyncMock, return_value=result):
        recovered = await _maybe_llm_recover(filing, reason="address")
    assert recovered is True
    assert filing.property_address == "123 Main St, Houston, TX 77002"


@pytest.mark.asyncio
async def test_high_confidence_name_recovery_mutates_filing(monkeypatch):
    monkeypatch.setenv("LLM_RECOVERY_ENABLED", "true")
    filing = _filing(tenant_name="GARCIA, MARIA AND ALL OTHER OCCUPANTS")
    result = RecoveryResult(
        first="Maria", last="Garcia",
        confidence=0.9,
    )
    with patch("services.llm_recovery_service.recover",
               new_callable=AsyncMock, return_value=result):
        recovered = await _maybe_llm_recover(filing, reason="name")
    assert recovered is True
    assert filing.tenant_name == "Maria Garcia"


@pytest.mark.asyncio
async def test_cleaned_output_still_failing_gate_rejects(monkeypatch):
    """LLM returns high confidence but cleaned output STILL fails the gate
    (e.g. claims confidence on a name that's actually a business). We must
    re-run the gate and reject if it still fails."""
    monkeypatch.setenv("LLM_RECOVERY_ENABLED", "true")
    filing = _filing(tenant_name="Acme Properties LLC")
    # LLM hallucinates a recovery for a business name
    result = RecoveryResult(
        first="Acme", last="Properties LLC",
        confidence=0.95,
    )
    with patch("services.llm_recovery_service.recover",
               new_callable=AsyncMock, return_value=result):
        recovered = await _maybe_llm_recover(filing, reason="name")
    assert recovered is False  # gate_name still rejects entity terms
    assert filing.tenant_name == "Acme Properties LLC"


@pytest.mark.asyncio
async def test_address_recovery_requires_street_and_zip(monkeypatch):
    monkeypatch.setenv("LLM_RECOVERY_ENABLED", "true")
    filing = _filing(property_address="bad")
    result = RecoveryResult(
        street="", city="Houston", state="TX", zip="",  # missing both
        confidence=0.9,
    )
    with patch("services.llm_recovery_service.recover",
               new_callable=AsyncMock, return_value=result):
        recovered = await _maybe_llm_recover(filing, reason="address")
    assert recovered is False
