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
