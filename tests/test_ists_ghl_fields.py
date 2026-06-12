import pytest


class _Resp:
    status_code = 201
    text = "ok"

    def json(self):
        return {"contact": {"id": "c1"}, "pipelines": []}


class _Client:
    def __init__(self, captured):
        self._captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def get(self, *a, **k):
        return _Resp()

    async def post(self, url, json, headers):
        if url.endswith("/contacts/upsert"):
            self._captured["payload"] = json
        return _Resp()


@pytest.mark.asyncio
async def test_ists_push_contact_populates_rent_case_landlord_judgment(monkeypatch):
    from services import ists_ghl

    captured: dict = {}
    monkeypatch.setattr(ists_ghl, "_LOCATION_ID", "loc")
    monkeypatch.setattr(ists_ghl, "_API_KEY", "key")
    monkeypatch.setattr(ists_ghl.httpx, "AsyncClient", lambda **k: _Client(captured))

    rec = {
        "case_number": "264100196540", "defendant_name": "Gamez, Silvio",
        "property_address": "20525 Ella Blvd, Spring, TX 77388", "phone": "3463710233",
        "plaintiff_name": "Ella REH LLC", "judgment_against": "Silvio Gamez",
        "estimated_rent": 1676.0, "judgment_date": "2026-06-02",
        "state": "TX", "county": "Harris", "language_hint": "english_likely",
    }
    await ists_ghl.push_contact(rec)

    cf = {c["id"]: c["field_value"] for c in captured["payload"]["customFields"]}
    F = ists_ghl._FIELD_IDS
    assert cf[F["monthly_rent"]] == "1676"          # rent now reaches GHL (was missing entirely)
    assert cf[F["case_number"]] == "264100196540"
    assert cf[F["landlord_name"]] == "Ella REH LLC"
    assert cf[F["judgment_against"]] == "Silvio Gamez"
    assert cf[F["judgment_date"]] == "2026-06-02"   # dedicated field, not Court Date
    assert cf[F["judgment_year"]] == "2026"
    assert cf[F["judgment_month"]] == "06"
