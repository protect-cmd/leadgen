"""SearchBug name_mismatch responses now include the extracted phone so the
runner can route them to a review stage instead of dropping them."""
from __future__ import annotations
import pytest
from services import searchbug_service


class _Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload
        self.text = ""

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
async def test_name_mismatch_returns_phone_and_address(monkeypatch):
    payload = {
        "rows": 1,
        "Status": "OK",
        "people": {
            "person": [
                {
                    "names": {"name": [{"firstName": "Bob", "lastName": "Smith"}]},
                    "phones": {"phone": [{"phoneType": "Mobile", "phoneNumber": "5559998888"}]},
                    "addresses": {"address": [{
                        "fullStreet": "1 Other Pl",
                        "city": "Houston", "state": "TX", "zip": "77002",
                        "lastDate": "01/01/2025"
                    }]},
                }
            ]
        },
    }
    monkeypatch.setenv("SEARCHBUG_CO_CODE", "co")
    monkeypatch.setenv("SEARCHBUG_API_KEY", "key")
    monkeypatch.setattr(
        searchbug_service.httpx, "AsyncClient", lambda **kw: _Client(payload)
    )
    searchbug_service.reset_circuit_breaker_for_tests()

    result = await searchbug_service.search_tenant_detailed(
        "Maria", "Garcia", "Houston", "TX", "77002", address="123 Main St"
    )

    assert result.status == "name_mismatch"
    assert result.phone == "5559998888"
    assert result.resolved_address is not None
    assert "Other Pl" in result.resolved_address
