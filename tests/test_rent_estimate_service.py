from __future__ import annotations

import pytest

from models.filing import Filing


def _filing() -> Filing:
    return Filing(
        case_number="TEST-RENT-001",
        tenant_name="Jane Tenant",
        property_address="123 Main St, Houston, TX 77002",
        landlord_name="Grant Owner",
        filing_date="2026-05-06",
        state="TX",
        county="Harris",
        notice_type="Eviction",
        source_url="https://example.test",
    )


def test_rent_precheck_defaults_to_disabled(monkeypatch):
    from services import rent_estimate_service

    monkeypatch.delenv("RENT_PRECHECK_ENABLED", raising=False)

    assert rent_estimate_service.is_enabled() is False


@pytest.mark.asyncio
async def test_rentometer_returns_median_rent(monkeypatch):
    from services import rent_estimate_service

    captured: dict = {}

    class Response:
        status_code = 200
        text = "{}"

        def json(self):
            return {"median": 1750, "mean": 1800, "samples": 8}

    class Client:
        def __init__(self, **kwargs):
            captured["timeout"] = kwargs["timeout"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, params):
            captured["url"] = url
            captured["params"] = params
            return Response()

    monkeypatch.setenv("RENT_PRECHECK_ENABLED", "true")
    monkeypatch.setenv("RENT_PRECHECK_PROVIDER", "rentometer")
    monkeypatch.setenv("RENTOMETER_API_KEY", "test-key")
    monkeypatch.setattr(rent_estimate_service.httpx, "AsyncClient", Client)

    rent = await rent_estimate_service.estimate_rent(_filing())

    assert rent == 1750.0
    assert captured["url"] == "https://www.rentometer.com/api/v1/summary"
    assert captured["params"]["api_key"] == "test-key"
    assert captured["params"]["address"] == "123 Main St, Houston, TX 77002"
    assert captured["params"]["bedrooms"] == "2"


@pytest.mark.asyncio
async def test_rent_precheck_fails_open_when_key_missing(monkeypatch):
    from services import rent_estimate_service

    monkeypatch.setenv("RENT_PRECHECK_ENABLED", "true")
    monkeypatch.setenv("RENT_PRECHECK_PROVIDER", "rentometer")
    monkeypatch.delenv("RENTOMETER_API_KEY", raising=False)

    assert await rent_estimate_service.estimate_rent(_filing()) is None


@pytest.mark.asyncio
async def test_rent_precheck_fails_open_when_provider_response_is_invalid(monkeypatch):
    from services import rent_estimate_service

    class Response:
        status_code = 200
        text = "not json"

        def json(self):
            raise ValueError("invalid json")

    class Client:
        def __init__(self, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, params):
            return Response()

    monkeypatch.setenv("RENT_PRECHECK_ENABLED", "true")
    monkeypatch.setenv("RENT_PRECHECK_PROVIDER", "rentometer")
    monkeypatch.setenv("RENTOMETER_API_KEY", "test-key")
    monkeypatch.setattr(rent_estimate_service.httpx, "AsyncClient", Client)

    assert await rent_estimate_service.estimate_rent(_filing()) is None
