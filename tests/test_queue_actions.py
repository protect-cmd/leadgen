import pytest
from fastapi import HTTPException

from dashboard.main import _case_numbers_required
from services.queue_actions import limited_case_numbers, rent_cases_track


class Result:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.filters = {}
        self.payload = None

    def select(self, *_args, **_kwargs):
        return self

    def update(self, payload):
        self.payload = payload
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, _value):
        return self

    def execute(self):
        case_number = self.filters.get("case_number")
        if self.payload is not None:
            self.db.updates.append((self.table, case_number, self.payload))
            row = self.db.rows[self.table][case_number]
            row.update(self.payload)
            return Result([row])
        row = self.db.rows.get(self.table, {}).get(case_number)
        return Result([row] if row else [])


class FakeSupabase:
    def __init__(self, rows):
        self.rows = rows
        self.updates = []

    def table(self, name):
        return Query(self, name)


def test_limited_case_numbers_dedupes_and_caps():
    cases, capped = limited_case_numbers([" A ", "A", "", None, "B", "C"], cap=2)

    assert cases == ["A", "B"]
    assert capped is True


def test_case_numbers_required_rejects_empty_payload():
    with pytest.raises(HTTPException) as exc:
        _case_numbers_required({"case_numbers": []})

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_rent_cases_track_updates_vantage_filings(monkeypatch):
    sb = FakeSupabase({
        "filings": {
            "CN1": {
                "case_number": "CN1",
                "tenant_name": "Alex Tenant",
                "landlord_name": "Owner",
                "property_address": "100 Main St, Houston, TX 77002",
                "filing_date": "2026-06-09",
                "court_date": "2026-06-30",
                "state": "TX",
                "county": "Harris",
                "notice_type": "Eviction",
                "source_url": "",
                "estimated_rent": None,
                "property_type": "residential",
            }
        }
    })

    async def fake_estimate(filing):
        assert filing.case_number == "CN1"
        return 1850.0

    monkeypatch.setattr("services.rent_estimate_service.estimate_rent", fake_estimate)

    payload = await rent_cases_track(sb, ["CN1"], track="vantage", cap=50)

    assert payload["summary"] == {"rent_found": 1}
    assert payload["results"][0]["rent"] == 1850.0
    assert sb.updates == [("filings", "CN1", {"estimated_rent": 1850.0})]


@pytest.mark.asyncio
async def test_rent_cases_track_updates_ists_judgments(monkeypatch):
    sb = FakeSupabase({
        "ists_judgments": {
            "ISTS1": {
                "case_number": "ISTS1",
                "defendant_name": "Jamie Tenant",
                "property_address": "200 Oak St, Nashville, TN 37209",
                "judgment_date": "2026-06-09",
                "state": "TN",
                "county": "Davidson",
                "estimated_rent": None,
            }
        }
    })

    async def fake_estimate(filing):
        assert filing.tenant_name == "Jamie Tenant"
        return 1650.0

    monkeypatch.setattr("services.rent_estimate_service.estimate_rent", fake_estimate)

    payload = await rent_cases_track(sb, ["ISTS1"], track="ists", cap=50)

    assert payload["summary"] == {"rent_found": 1}
    assert sb.updates == [("ists_judgments", "ISTS1", {"estimated_rent": 1650.0})]
