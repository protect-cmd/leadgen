from __future__ import annotations

from datetime import date

import pytest

from models.contact import EnrichedContact
from models.filing import Filing
from services import ghl_service


def _contact() -> EnrichedContact:
    filing = Filing(
        case_number="TEST-GHL-OPP-WARN",
        tenant_name="Jane Tenant",
        property_address="123 Main St, Houston, TX 77002",
        landlord_name="Grant Owner",
        filing_date=date(2026, 5, 7),
        court_date=date(2026, 5, 10),
        state="TX",
        county="Harris",
        notice_type="Eviction",
        source_url="https://example.test",
    )
    return EnrichedContact(
        filing=filing,
        track="ec",
        phone="+17135550100",
        estimated_rent=1800,
        property_type="residential",
    )


@pytest.mark.asyncio
async def test_create_contact_warns_when_opportunity_creation_fails(monkeypatch):
    alerts: list[tuple[str, str, str]] = []

    class Response:
        def __init__(self, status_code: int, payload: dict | None = None, text: str = "ok"):
            self.status_code = status_code
            self._payload = payload or {}
            self.text = text

        def json(self):
            return self._payload

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, params, headers):
            return Response(
                200,
                {
                    "pipelines": [
                        {
                            "id": "pipeline-1",
                            "name": "New Filings",
                            "stages": [{"id": "stage-1", "name": "New Filing"}],
                        }
                    ]
                },
            )

        async def post(self, url, json, headers):
            if url.endswith("/contacts/upsert"):
                return Response(201, {"contact": {"id": "contact-1"}})
            if url.endswith("/notes"):
                return Response(201, {})
            if url.endswith("/opportunities/"):
                return Response(400, text='{"message":"The pipeline id is invalid."}')
            raise AssertionError(f"Unexpected POST {url}")

    async def send_job_error(*, job: str, stage: str, error, priority: int = 1):
        alerts.append((job, stage, str(error)))
        return True

    monkeypatch.setenv("GHL_API_KEY", "test-key")
    monkeypatch.setenv("GHL_EC_LOCATION_ID", "loc-1")
    monkeypatch.setattr(ghl_service.httpx, "AsyncClient", lambda **kwargs: Client())
    monkeypatch.setattr(ghl_service.notification_service, "send_job_error", send_job_error)
    ghl_service._pipeline_cache.clear()

    contact_id = await ghl_service.create_contact(_contact(), ["EC-New-Filing"], "stage-1")

    assert contact_id == "contact-1"
    assert alerts == [
        (
            "TX/Harris",
            "ghl_opportunity_ec",
            'GHL opportunity creation failed 400: {"message":"The pipeline id is invalid."}',
        )
    ]


@pytest.mark.asyncio
async def test_create_contact_does_not_alert_for_duplicate_opportunity(monkeypatch):
    alerts: list[tuple[str, str, str]] = []

    class Response:
        def __init__(self, status_code: int, payload: dict | None = None, text: str = "ok"):
            self.status_code = status_code
            self._payload = payload or {}
            self.text = text

        def json(self):
            return self._payload

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, params, headers):
            return Response(
                200,
                {
                    "pipelines": [
                        {
                            "id": "pipeline-1",
                            "name": "New Filings",
                            "stages": [{"id": "stage-1", "name": "New Filing"}],
                        }
                    ]
                },
            )

        async def post(self, url, json, headers):
            if url.endswith("/contacts/upsert"):
                return Response(201, {"contact": {"id": "contact-1"}})
            if url.endswith("/notes"):
                return Response(201, {})
            if url.endswith("/opportunities/"):
                return Response(
                    400,
                    text='{"message":"Can not create duplicate opportunity for the contact."}',
                )
            raise AssertionError(f"Unexpected POST {url}")

    async def send_job_error(*, job: str, stage: str, error, priority: int = 1):
        alerts.append((job, stage, str(error)))
        return True

    monkeypatch.setenv("GHL_API_KEY", "test-key")
    monkeypatch.setenv("GHL_EC_LOCATION_ID", "loc-1")
    monkeypatch.setattr(ghl_service.httpx, "AsyncClient", lambda **kwargs: Client())
    monkeypatch.setattr(ghl_service.notification_service, "send_job_error", send_job_error)
    ghl_service._pipeline_cache.clear()

    contact_id = await ghl_service.create_contact(_contact(), ["EC-New-Filing"], "stage-1")

    assert contact_id == "contact-1"
    assert alerts == []


@pytest.mark.asyncio
async def test_create_contact_rejects_contact_without_phone_or_email():
    contact = _contact()
    contact.phone = None
    contact.email = None

    with pytest.raises(RuntimeError, match="phone or email"):
        await ghl_service.create_contact(contact, ["EC-New-Filing"], "stage-1")


@pytest.mark.asyncio
async def test_create_contact_pushes_custom_fields_by_id_for_ng_track(monkeypatch):
    """NG contacts should send customFields as {id, field_value} using the
    hardcoded UUID map. Without UUIDs the v2 API silently drops the values."""
    captured: dict = {}

    class Response:
        def __init__(self, status_code: int, payload: dict | None = None, text: str = "ok"):
            self.status_code = status_code
            self._payload = payload or {}
            self.text = text
        def json(self):
            return self._payload

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def get(self, url, params, headers):
            return Response(200, {"pipelines": [
                {"id": "pip", "name": "X",
                 "stages": [{"id": "stage-1", "name": "New"}]}
            ]})
        async def post(self, url, json, headers):
            if url.endswith("/contacts/upsert"):
                captured["upsert"] = json
                return Response(201, {"contact": {"id": "c-1"}})
            return Response(201, {})

    # Patch the field-ID map to a known set so the test is independent of
    # production field IDs.
    monkeypatch.setattr(ghl_service, "_NG_FIELD_IDS", {
        "filing_county": "ID_COUNTY",
        "case_number": "ID_CASE",
        "filing_year": "ID_YEAR",
        "filing_month": "ID_MONTH",
        "filing_day": "ID_DAY",
        "filing_date": "ID_FILING_DATE",
        "court_date": "ID_COURT_DATE",
        "tenant_name": "ID_TENANT_NAME",
        "landlord_name": "ID_LANDLORD_NAME",
        "monthly_rent_amount": "ID_RENT",
        "notice_type": "ID_NOTICE",
        "language_preference": "ID_LANG",
    })
    monkeypatch.setenv("GHL_API_KEY", "test-key")
    monkeypatch.setenv("GHL_NG_LOCATION_ID", "loc-ng")
    monkeypatch.setattr(ghl_service.httpx, "AsyncClient", lambda **kwargs: Client())
    ghl_service._pipeline_cache.clear()

    contact = _contact()
    contact.track = "ng"  # force NG track for this test

    await ghl_service.create_contact(contact, ["NG-New-Filing"], "stage-1")

    custom_fields = captured["upsert"]["customFields"]
    # Every entry must use {id, field_value} format (no 'key' field)
    for cf in custom_fields:
        assert "id" in cf and "field_value" in cf, f"bad shape: {cf}"
        assert "key" not in cf

    by_id = {f["id"]: f["field_value"] for f in custom_fields}
    # _contact() filing_date is date(2026, 5, 7), court_date is date(2026, 5, 10)
    assert by_id["ID_COUNTY"] == "Harris"
    assert by_id["ID_CASE"] == "TEST-GHL-OPP-WARN"
    assert by_id["ID_YEAR"] == "2026"
    assert by_id["ID_MONTH"] == "05"
    assert by_id["ID_DAY"] == "07"
    assert by_id["ID_FILING_DATE"] == "2026-05-07"
    assert by_id["ID_COURT_DATE"] == "2026-05-10"
    assert by_id["ID_TENANT_NAME"] == "Jane Tenant"
    assert by_id["ID_LANDLORD_NAME"] == "Grant Owner"
    assert by_id["ID_RENT"] == 1800              # estimated_rent now reaches GHL
    assert by_id["ID_NOTICE"] == "Eviction"
    assert by_id["ID_LANG"] == "English"         # language_hint None -> English


@pytest.mark.asyncio
async def test_create_contact_ec_track_skips_custom_fields(monkeypatch):
    """EC track has no field-ID mapping yet (track is disabled). Should
    not crash, just send an empty customFields list."""
    captured: dict = {}

    class Response:
        def __init__(self, status_code, payload=None, text="ok"):
            self.status_code = status_code
            self._payload = payload or {}
            self.text = text
        def json(self): return self._payload

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def get(self, url, params, headers):
            return Response(200, {"pipelines": [
                {"id": "pip", "name": "X", "stages": [{"id": "stage-1", "name": "New"}]}
            ]})
        async def post(self, url, json, headers):
            if url.endswith("/contacts/upsert"):
                captured["upsert"] = json
                return Response(201, {"contact": {"id": "c-1"}})
            return Response(201, {})

    monkeypatch.setenv("GHL_API_KEY", "test-key")
    monkeypatch.setenv("GHL_EC_LOCATION_ID", "loc-1")
    monkeypatch.setattr(ghl_service.httpx, "AsyncClient", lambda **kwargs: Client())
    ghl_service._pipeline_cache.clear()

    await ghl_service.create_contact(_contact(), ["EC-New-Filing"], "stage-1")

    # EC track uses an empty field map -> no custom fields sent
    assert captured["upsert"]["customFields"] == []
