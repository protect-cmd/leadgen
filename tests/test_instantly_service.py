from __future__ import annotations

from datetime import date

import httpx
import pytest

from models.contact import EnrichedContact
from models.filing import Filing
from services import instantly_service


def _contact(**kwargs) -> EnrichedContact:
    filing = Filing(
        case_number="TEST-INSTANTLY-001",
        tenant_name="Jane Tenant",
        property_address="123 Main St, Houston, TX 77002",
        landlord_name="Grant Owner",
        filing_date=date(2026, 5, 6),
        state="TX",
        county="Harris",
        notice_type="Eviction",
        source_url="https://example.test",
    )
    values = {
        "filing": filing,
        "track": "ec",
        "email": "owner@example.test",
    }
    values.update(kwargs)
    return EnrichedContact(**values)


def test_instantly_disabled_without_flag(monkeypatch):
    monkeypatch.setenv("INSTANTLY_API_KEY", "key")
    monkeypatch.delenv("INSTANTLY_ENABLED", raising=False)

    assert instantly_service.is_enabled() is False


def test_instantly_enabled_with_flag(monkeypatch):
    monkeypatch.setenv("INSTANTLY_ENABLED", "true")

    assert instantly_service.is_enabled() is True


@pytest.mark.asyncio
async def test_enroll_skips_when_disabled(monkeypatch):
    monkeypatch.setenv("INSTANTLY_API_KEY", "key")
    monkeypatch.delenv("INSTANTLY_ENABLED", raising=False)

    result = await instantly_service.enroll(_contact())

    assert result.enrolled is False
    assert result.skipped_reason == "disabled"


@pytest.mark.asyncio
async def test_enroll_skips_missing_email(monkeypatch):
    monkeypatch.setenv("INSTANTLY_ENABLED", "true")

    result = await instantly_service.enroll(_contact(email=None))

    assert result.enrolled is False
    assert result.skipped_reason == "missing_email"


@pytest.mark.asyncio
async def test_enroll_skips_missing_campaign_id(monkeypatch):
    monkeypatch.setenv("INSTANTLY_ENABLED", "true")
    monkeypatch.delenv("INSTANTLY_EC_CAMPAIGN_ID", raising=False)

    result = await instantly_service.enroll(_contact())

    assert result.enrolled is False
    assert result.skipped_reason == "missing_campaign_id"


@pytest.mark.asyncio
async def test_enroll_posts_campaign_payload(monkeypatch):
    posts: list[dict] = []

    class Response:
        text = "ok"
        status_code = 200

        def json(self):
            return {"leads_uploaded": 1, "duplicated_leads": 0, "in_blocklist": 0}

        def raise_for_status(self):
            return None

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers, json):
            posts.append({"url": url, "headers": headers, "json": json})
            return Response()

    monkeypatch.setenv("INSTANTLY_ENABLED", "true")
    monkeypatch.setenv("INSTANTLY_API_KEY", "api-key")
    monkeypatch.setenv("INSTANTLY_EC_CAMPAIGN_ID", "campaign-ec")
    monkeypatch.setattr(instantly_service.httpx, "AsyncClient", lambda **kwargs: Client())

    result = await instantly_service.enroll(_contact())

    assert result.enrolled is True
    assert result.error is None
    assert posts[0]["url"] == f"{instantly_service.BASE}/leads/add"
    assert posts[0]["headers"]["Authorization"] == "Bearer api-key"
    assert posts[0]["json"]["campaign_id"] == "campaign-ec"
    assert posts[0]["json"]["leads"][0]["email"] == "owner@example.test"
    assert posts[0]["json"]["leads"][0]["custom_variables"]["case_number"] == "TEST-INSTANTLY-001"


@pytest.mark.asyncio
async def test_enroll_reports_duplicate_as_skip(monkeypatch):
    result = await _run_with_response(
        monkeypatch,
        {"leads_uploaded": 0, "duplicated_leads": 1, "in_blocklist": 0},
    )

    assert result.enrolled is False
    assert result.skipped_reason == "duplicate"


@pytest.mark.asyncio
async def test_enroll_reports_blocklist_as_skip(monkeypatch):
    result = await _run_with_response(
        monkeypatch,
        {"leads_uploaded": 0, "duplicated_leads": 0, "in_blocklist": 1},
    )

    assert result.enrolled is False
    assert result.skipped_reason == "blocklisted"


@pytest.mark.asyncio
async def test_enroll_returns_error_on_http_failure(monkeypatch):
    class Response:
        text = "bad request"
        status_code = 400

        def raise_for_status(self):
            request = httpx.Request("POST", f"{instantly_service.BASE}/leads/add")
            raise httpx.HTTPStatusError("bad", request=request, response=self)

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setenv("INSTANTLY_ENABLED", "true")
    monkeypatch.setenv("INSTANTLY_API_KEY", "api-key")
    monkeypatch.setenv("INSTANTLY_EC_CAMPAIGN_ID", "campaign-ec")
    monkeypatch.setattr(instantly_service.httpx, "AsyncClient", lambda **kwargs: Client())

    result = await instantly_service.enroll(_contact())

    assert result.enrolled is False
    assert result.error == "owner@example.test [EC]: HTTP 400"


async def _run_with_response(monkeypatch, payload: dict) -> instantly_service.InstantlyResult:
    class Response:
        text = "ok"
        status_code = 200

        def json(self):
            return payload

        def raise_for_status(self):
            return None

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setenv("INSTANTLY_ENABLED", "true")
    monkeypatch.setenv("INSTANTLY_API_KEY", "api-key")
    monkeypatch.setenv("INSTANTLY_EC_CAMPAIGN_ID", "campaign-ec")
    monkeypatch.setattr(instantly_service.httpx, "AsyncClient", lambda **kwargs: Client())

    return await instantly_service.enroll(_contact())
