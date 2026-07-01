import pytest
from fastapi import HTTPException

from dashboard.main import _case_numbers_required
from services.queue_actions import (
    _is_searchbug_paid_hit,
    limited_case_numbers,
    rent_cases_track,
)


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

    @property
    def not_(self):
        return self

    def is_(self, *_args, **_kwargs):
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


def test_searchbug_paid_hit_accounting_is_conservative():
    assert _is_searchbug_paid_hit("phone_found") is True
    assert _is_searchbug_paid_hit("name_mismatch") is True
    assert _is_searchbug_paid_hit("ambiguous") is True
    assert _is_searchbug_paid_hit("no_phone") is True
    assert _is_searchbug_paid_hit("no_records") is False
    assert _is_searchbug_paid_hit("skipped") is False


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

    monkeypatch.setattr("services.rent_estimate_service.is_enabled", lambda: True)
    monkeypatch.setenv("RENTOMETER_API_KEY", "test-key")
    monkeypatch.setattr("services.rent_estimate_service.estimate_rent", fake_estimate)

    payload = await rent_cases_track(sb, ["CN1"], track="vantage", cap=50)

    assert payload["summary"] == {"rent_found": 1}
    assert payload["results"][0]["rent"] == 1850.0
    assert sb.updates == [("filings", "CN1", {"estimated_rent": 1850.0})]


@pytest.mark.asyncio
async def test_rent_cases_track_reports_disabled_rent_precheck(monkeypatch):
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

    monkeypatch.setattr("services.rent_estimate_service.is_enabled", lambda: False)

    payload = await rent_cases_track(sb, ["CN1"], track="vantage", cap=50)

    assert payload["summary"] == {"rent_disabled": 1}
    assert payload["results"][0]["status"] == "rent_disabled"
    assert sb.updates == []


@pytest.mark.asyncio
async def test_rent_cases_track_reports_missing_rentometer_key(monkeypatch):
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

    monkeypatch.setattr("services.rent_estimate_service.is_enabled", lambda: True)
    monkeypatch.delenv("RENTOMETER_API_KEY", raising=False)

    payload = await rent_cases_track(sb, ["CN1"], track="vantage", cap=50)

    assert payload["summary"] == {"rent_key_missing": 1}
    assert payload["results"][0]["status"] == "rent_key_missing"
    assert sb.updates == []


@pytest.mark.asyncio
async def test_enrich_vantage_reports_skipped_when_searchbug_not_called(monkeypatch):
    """A lead the cost gates declined to query must read 'skipped', not 'no_phone'."""
    from models.contact import EnrichedContact
    from services.queue_actions import enrich_cases_track

    sb = FakeSupabase({
        "filings": {
            "CN1": {
                "case_number": "CN1",
                "tenant_name": "Alex Smith",
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
                "language_hint": "en",
            }
        }
    })

    async def fake_enrich_tenant(filing, **kwargs):
        # searchbug_status None == cost gates skipped the call entirely.
        return EnrichedContact(filing=filing, track="ng", phone=None, searchbug_status=None)

    async def fake_update_enrichment(_contact):
        return None

    monkeypatch.setattr("services.batchdata_service.enrich_tenant", fake_enrich_tenant)
    monkeypatch.setattr("services.dedup_service.update_enrichment", fake_update_enrichment)

    payload = await enrich_cases_track(sb, ["CN1"], track="vantage", cap=25)

    assert payload["summary"] == {"skipped": 1}
    assert payload["results"][0]["status"] == "skipped"
    assert payload["results"][0]["phone_found"] is False
    assert payload["results"][0]["paid_hit"] is False


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

    monkeypatch.setattr("services.rent_estimate_service.is_enabled", lambda: True)
    monkeypatch.setenv("RENTOMETER_API_KEY", "test-key")
    monkeypatch.setattr("services.rent_estimate_service.estimate_rent", fake_estimate)

    payload = await rent_cases_track(sb, ["ISTS1"], track="ists", cap=50)

    assert payload["summary"] == {"rent_found": 1}
    assert sb.updates == [("ists_judgments", "ISTS1", {"estimated_rent": 1650.0})]


@pytest.mark.asyncio
async def test_enrich_ists_uses_guarded_searchbug_path(monkeypatch):
    from models.contact import EnrichedContact
    from services.queue_actions import enrich_cases_track

    sb = FakeSupabase({
        "ists_judgments": {
            "ISTS1": {
                "case_number": "ISTS1",
                "defendant_name": "Jamie Tenant",
                "property_address": "200 Oak St, Nashville, TN 37209",
                "judgment_date": "2026-06-09",
                "state": "TN",
                "county": "Davidson",
                "estimated_rent": 1650.0,
                "phone": None,
            }
        }
    })

    async def fake_enrich_tenant(filing, **kwargs):
        assert filing.tenant_name == "Jamie Tenant"
        assert kwargs["lookup_property_if_missing"] is False
        return EnrichedContact(
            filing=filing,
            track="ng",
            phone="5551234567",
            searchbug_status="phone_found",
        )

    monkeypatch.setattr("services.batchdata_service.enrich_tenant", fake_enrich_tenant)
    monkeypatch.setattr("services.dnc_service.verdict", lambda phone: "callable")

    payload = await enrich_cases_track(sb, ["ISTS1"], track="ists", cap=25)

    assert payload["summary"] == {"phone_found": 1}
    assert payload["results"][0]["dnc_status"] == "callable"
    assert payload["results"][0]["paid_hit"] is True
    assert sb.rows["ists_judgments"]["ISTS1"]["phone"] == "5551234567"


@pytest.mark.asyncio
async def test_enrich_ists_does_not_store_name_mismatch_phone(monkeypatch):
    from models.contact import EnrichedContact
    from services.queue_actions import enrich_cases_track

    sb = FakeSupabase({
        "ists_judgments": {
            "ISTS1": {
                "case_number": "ISTS1",
                "defendant_name": "Jamie Tenant",
                "property_address": "200 Oak St, Nashville, TN 37209",
                "judgment_date": "2026-06-09",
                "state": "TN",
                "county": "Davidson",
                "estimated_rent": 1650.0,
                "phone": None,
            }
        }
    })

    async def fake_enrich_tenant(filing, **kwargs):
        return EnrichedContact(
            filing=filing,
            track="ng",
            phone="5559990000",
            searchbug_status="name_mismatch",
        )

    monkeypatch.setattr("services.batchdata_service.enrich_tenant", fake_enrich_tenant)

    payload = await enrich_cases_track(sb, ["ISTS1"], track="ists", cap=25)

    assert payload["summary"] == {"name_mismatch": 1}
    assert payload["results"][0]["phone_found"] is False
    assert payload["results"][0]["paid_hit"] is True
    assert sb.rows["ists_judgments"]["ISTS1"].get("phone") is None
