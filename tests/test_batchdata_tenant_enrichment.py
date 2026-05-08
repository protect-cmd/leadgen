from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.filing import Filing
from services import batchdata_service


def _filing(**kwargs) -> Filing:
    values = {
        "case_number": "TEST-TENANT-001",
        "tenant_name": "Maria Test Tenant",
        "property_address": "456 Oak Ave, Columbia, SC 29201",
        "landlord_name": "Grant Test Owner",
        "filing_date": date(2026, 5, 9),
        "state": "SC",
        "county": "Richland",
        "notice_type": "Eviction",
        "source_url": "https://example.test",
        "property_type_hint": "residential",
    }
    values.update(kwargs)
    return Filing(**values)


def _make_response(persons: list[dict], status_code: int = 200) -> MagicMock:
    """Build a mock httpx Response with the given persons list."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = {"results": {"persons": persons}}
    mock_resp.text = ""
    return mock_resp


def _person(full_name: str, phone_number: str = "5550001234", dnc: bool = False) -> dict:
    return {
        "fullName": full_name,
        "phoneNumbers": [{"number": phone_number, "type": "Mobile", "score": 90, "dnc": dnc}],
        "emails": [],
    }



@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    """Ensure BATCHDATA_API_KEY is set so _headers() doesn't raise."""
    monkeypatch.setenv("BATCHDATA_API_KEY", "test-key")


@pytest.mark.asyncio
async def test_tenant_skip_trace_exact_name_match():
    """Exact name match → phone is returned."""
    filing = _filing(tenant_name="Maria Test Tenant")
    mock_resp = _make_response([_person("Maria Test Tenant")])

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        result = await batchdata_service.enrich_tenant(filing, lookup_property_if_missing=False)

    assert result.phone is not None
    assert result.phone == "5550001234"


@pytest.mark.asyncio
async def test_tenant_skip_trace_normalized_name_match():
    """Case-insensitive name match → phone is returned."""
    filing = _filing(tenant_name="MARIA TEST TENANT")
    mock_resp = _make_response([_person("Maria Test Tenant")])

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        result = await batchdata_service.enrich_tenant(filing, lookup_property_if_missing=False)

    assert result.phone is not None


@pytest.mark.asyncio
async def test_tenant_skip_trace_rejects_owner_fallback():
    """Owner name that doesn't match the tenant → no phone, dnc_status stays unknown."""
    filing = _filing(tenant_name="Maria Test Tenant")
    mock_resp = _make_response([_person("Grant Test Owner")])

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        result = await batchdata_service.enrich_tenant(filing, lookup_property_if_missing=False)

    assert result.phone is None
    assert result.dnc_status == "unknown"


@pytest.mark.asyncio
async def test_tenant_skip_trace_rejects_llc():
    """LLC/corporate entity name → rejected even if API returns it."""
    filing = _filing(tenant_name="Maria Test Tenant")
    mock_resp = _make_response([_person("Property Holdings LLC")])

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        result = await batchdata_service.enrich_tenant(filing, lookup_property_if_missing=False)

    assert result.phone is None


@pytest.mark.asyncio
async def test_tenant_skip_trace_preserves_dnc_status():
    """Matched tenant phone with dnc=False → dnc_status is 'clear'."""
    filing = _filing(tenant_name="Maria Test Tenant")
    mock_resp = _make_response([_person("Maria Test Tenant", phone_number="5550009999", dnc=False)])

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        result = await batchdata_service.enrich_tenant(filing, lookup_property_if_missing=False)

    assert result.dnc_status == "clear"
