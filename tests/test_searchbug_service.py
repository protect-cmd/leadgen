from __future__ import annotations

import pytest

from services import searchbug_service


def test_best_phone_ignores_null_phone_entries():
    assert (
        searchbug_service._best_phone(
            [
                None,
                {"phoneType": "Landline", "phoneNumber": "5551110000"},
            ]
        )
        == "5551110000"
    )


def test_most_recent_address_ignores_null_address_entries():
    result = searchbug_service._most_recent_address(
        [
            None,
            {"fullStreet": "123 Main St", "lastDate": "01/01/2024"},
        ]
    )

    assert result == {"fullStreet": "123 Main St", "lastDate": "01/01/2024"}


class _Response:
    status_code = 200
    text = '{"Status":"Error"}'

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, *args, **kwargs):
        return _Response(self.payload)


@pytest.mark.asyncio
async def test_search_tenant_detailed_reports_account_error(monkeypatch):
    payload = {
        "Status": "Error",
        "Error": "Prepaid Plan with balance is required. Error Code: 12730510",
    }
    monkeypatch.setenv("SEARCHBUG_CO_CODE", "co")
    monkeypatch.setenv("SEARCHBUG_API_KEY", "key")
    monkeypatch.setattr(
        searchbug_service.httpx,
        "AsyncClient",
        lambda **kwargs: _Client(payload),
    )

    result = await searchbug_service.search_tenant_detailed(
        "Ablessing",
        "Wesley",
        "Houston",
        "TX",
        "77008",
    )

    assert result.status == "account_error"
    assert result.retryable is True
    assert result.error_code == "12730510"


@pytest.mark.asyncio
async def test_search_tenant_detailed_reports_ambiguous(monkeypatch):
    payload = {"rows": 2, "people": {"person": []}}
    monkeypatch.setenv("SEARCHBUG_CO_CODE", "co")
    monkeypatch.setenv("SEARCHBUG_API_KEY", "key")
    monkeypatch.setattr(
        searchbug_service.httpx,
        "AsyncClient",
        lambda **kwargs: _Client(payload),
    )

    result = await searchbug_service.search_tenant_detailed(
        "Jane",
        "Doe",
        "Houston",
        "TX",
        "77008",
    )

    assert result.status == "ambiguous"
    assert result.retryable is False
