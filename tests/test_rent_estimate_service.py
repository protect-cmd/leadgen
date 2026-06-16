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


def _capture_client(captured: dict):
    class Response:
        status_code = 200
        text = "{}"

        def json(self):
            return {"median": 1750, "mean": 1800, "samples": 8}

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, params):
            captured["params"] = params
            return Response()

    return Client


@pytest.mark.asyncio
async def test_rentometer_forwards_valid_optional_params(monkeypatch):
    from services import rent_estimate_service

    captured: dict = {}
    monkeypatch.setenv("RENT_PRECHECK_ENABLED", "true")
    monkeypatch.setenv("RENTOMETER_API_KEY", "test-key")
    monkeypatch.setenv("RENTOMETER_BEDROOMS", "3")
    monkeypatch.setenv("RENTOMETER_BATHS", "1.5+")
    monkeypatch.setenv("RENTOMETER_BUILDING_TYPE", "house")
    monkeypatch.setattr(rent_estimate_service.httpx, "AsyncClient", _capture_client(captured))

    await rent_estimate_service.estimate_rent(_filing())

    assert captured["params"]["bedrooms"] == "3"
    assert captured["params"]["baths"] == "1.5+"
    assert captured["params"]["building_type"] == "house"


@pytest.mark.asyncio
async def test_rentometer_drops_invalid_optional_params(monkeypatch):
    from services import rent_estimate_service

    captured: dict = {}
    monkeypatch.setenv("RENT_PRECHECK_ENABLED", "true")
    monkeypatch.setenv("RENTOMETER_API_KEY", "test-key")
    monkeypatch.setenv("RENTOMETER_BEDROOMS", "9")          # invalid -> default 2
    monkeypatch.setenv("RENTOMETER_BATHS", "2")             # invalid -> dropped
    monkeypatch.setenv("RENTOMETER_BUILDING_TYPE", "apartments")  # plural -> dropped
    monkeypatch.setattr(rent_estimate_service.httpx, "AsyncClient", _capture_client(captured))

    await rent_estimate_service.estimate_rent(_filing())

    assert captured["params"]["bedrooms"] == "2"
    assert "baths" not in captured["params"]
    assert "building_type" not in captured["params"]


@pytest.fixture
def safmr_csv(tmp_path):
    path = tmp_path / "hud_safmr.csv"
    path.write_text(
        "zip,br0,br1,br2,br3,br4\n"
        "77005,1530,1610,1920,2390,2840\n"
        "45208,940,1000,1180,1480,1660\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.asyncio
async def test_hud_provider_returns_zip_safmr_2br_rent(monkeypatch, safmr_csv):
    from services import rent_estimate_service

    filing = _filing()
    filing.property_address = "6100 Main St, Houston, TX 77005"

    monkeypatch.setenv("RENT_PRECHECK_ENABLED", "true")
    monkeypatch.setenv("RENT_PRECHECK_PROVIDER", "hud")
    monkeypatch.setenv("HUD_SAFMR_DATA_PATH", str(safmr_csv))
    monkeypatch.delenv("HUD_SAFMR_BEDROOMS", raising=False)
    monkeypatch.delenv("RENTOMETER_BEDROOMS", raising=False)

    rent = await rent_estimate_service.estimate_rent(filing)

    assert rent == 1920.0


@pytest.mark.asyncio
async def test_hud_provider_honors_bedrooms(monkeypatch, safmr_csv):
    from services import rent_estimate_service

    filing = _filing()
    filing.property_address = "6100 Main St, Houston, TX 77005"

    monkeypatch.setenv("RENT_PRECHECK_ENABLED", "true")
    monkeypatch.setenv("RENT_PRECHECK_PROVIDER", "safmr")
    monkeypatch.setenv("HUD_SAFMR_DATA_PATH", str(safmr_csv))
    monkeypatch.setenv("HUD_SAFMR_BEDROOMS", "3")

    rent = await rent_estimate_service.estimate_rent(filing)

    assert rent == 2390.0


@pytest.mark.asyncio
async def test_hud_provider_fails_open_when_zip_not_in_table(monkeypatch, safmr_csv):
    from services import rent_estimate_service

    filing = _filing()
    filing.property_address = "999 Nowhere Rd, Houston, TX 70000"

    monkeypatch.setenv("RENT_PRECHECK_ENABLED", "true")
    monkeypatch.setenv("RENT_PRECHECK_PROVIDER", "hud")
    monkeypatch.setenv("HUD_SAFMR_DATA_PATH", str(safmr_csv))

    assert await rent_estimate_service.estimate_rent(filing) is None


@pytest.mark.asyncio
async def test_hud_provider_fails_open_when_data_file_missing(monkeypatch, tmp_path):
    from services import rent_estimate_service

    monkeypatch.setenv("RENT_PRECHECK_ENABLED", "true")
    monkeypatch.setenv("RENT_PRECHECK_PROVIDER", "hud")
    monkeypatch.setenv("HUD_SAFMR_DATA_PATH", str(tmp_path / "missing.csv"))

    assert await rent_estimate_service.estimate_rent(_filing()) is None


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
