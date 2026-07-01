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


class _Query:
    def __init__(self):
        self.calls = []

    def select(self, value):
        self.calls.append(("select", value))
        return self

    @property
    def not_(self):
        self.calls.append(("not_",))
        return self

    def is_(self, column, value):
        self.calls.append(("is_", column, value))
        return self

    def gte(self, column, value):
        self.calls.append(("gte", column, value))
        return self

    def limit(self, value):
        self.calls.append(("limit", value))
        return self

    def execute(self):
        self.calls.append(("execute",))
        return type("Resp", (), {"data": []})()


class _SB:
    def __init__(self, query):
        self.query = query

    def table(self, name):
        self.query.calls.append(("table", name))
        return self.query


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
    # lead-type + window + property-type tags (window defaults to W1)
    tags = captured["payload"]["tags"]
    assert "ISTS" in tags and "ists_new_lead" in tags
    assert "W1" in tags
    assert "Residential" in tags
    assert cf[F["situation"]] == "Judgment entered — Window 1"


@pytest.mark.asyncio
async def test_ists_push_contact_uses_window_2_when_record_is_w2(monkeypatch):
    from services import ists_ghl

    captured: dict = {}
    monkeypatch.setattr(ists_ghl, "_LOCATION_ID", "loc")
    monkeypatch.setattr(ists_ghl, "_API_KEY", "key")
    monkeypatch.setattr(ists_ghl.httpx, "AsyncClient", lambda **k: _Client(captured))

    rec = {
        "case_number": "X1", "defendant_name": "Doe, Jane",
        "property_address": "1 St, Houston, TX", "phone": "3460000000",
        "plaintiff_name": "Acme LLC", "judgment_date": "2026-06-02",
        "state": "TX", "county": "Harris", "window_tag": "W2",
    }
    await ists_ghl.push_contact(rec)

    tags = captured["payload"]["tags"]
    assert "W2" in tags and "W1" not in tags
    cf = {c["id"]: c["field_value"] for c in captured["payload"]["customFields"]}
    assert cf[ists_ghl._FIELD_IDS["situation"]] == "Judgment entered — Window 2"


@pytest.mark.asyncio
async def test_ists_push_batch_applies_judgment_freshness_gate(monkeypatch):
    from datetime import date, timedelta

    from services import ists_ghl

    query = _Query()
    monkeypatch.setattr(ists_ghl, "_client", _SB(query))
    monkeypatch.setattr(ists_ghl, "_FRESHNESS_DAYS", 14)

    await ists_ghl.push_batch(limit=7, dry_run=True)

    cutoff = (date.today() - timedelta(days=14)).isoformat()
    assert ("gte", "judgment_date", cutoff) in query.calls
    assert ("limit", 7) in query.calls
