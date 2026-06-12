import pytest

from services import fire_service


class _Cache:
    def __init__(self, under):
        self._under = under
        self.incremented = 0

    def check_daily_cap(self, cap, kind="searchbug"):
        return self._under

    def increment_daily_count(self, kind="searchbug"):
        self.incremented += 1


class _Q:
    def __init__(self, data):
        self._data = data
        self._payload = None

    def select(self, *a, **k):
        return self

    def update(self, payload):
        self._payload = payload
        return self

    def eq(self, *a, **k):
        return self

    def limit(self, *a):
        return self

    @property
    def not_(self):
        return self

    def is_(self, *a, **k):
        return self

    def execute(self):
        class R:
            pass
        r = R()
        r.data = [] if self._payload is not None else self._data
        return r


class _SB:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Q(list(self.tables.get(name, [])))


def _fake_sb():
    return _SB({
        "lead_contacts": [{"phone": "6155551234", "language_hint": None,
                            "ghl_contact_id": "ghl-1", "bland_call_id": None}],
        "filings": [{"case_number": "CN1", "tenant_name": "Alex Tenant",
                     "property_address": "100 Main St, Houston, TX 77002",
                     "landlord_name": "Owner", "filing_date": "2026-06-10",
                     "court_date": None, "state": "TX", "county": "Harris",
                     "notice_type": "Eviction", "source_url": "",
                     "estimated_rent": 2000, "property_type": "residential"}],
    })


@pytest.mark.asyncio
async def test_fire_case_blocked_but_ghl_staged_when_ceiling_hit(monkeypatch):
    cache = _Cache(under=False)               # over the daily Bland cap
    monkeypatch.setattr("services.enrichment_cache.get_cache", lambda: cache)
    monkeypatch.setattr("services.dnc_service.verdict", lambda phone: "callable")
    monkeypatch.setattr("services.call_window.in_call_window", lambda state, now_utc=None: True)

    res = await fire_service.fire_case(_fake_sb(), "CN1")

    # GHL was already staged (ghl-1); Bland is gated -> daily_cap, no dial, no increment
    assert res["status"] == "daily_cap"
    assert res["ghl_id"] == "ghl-1"
    assert cache.incremented == 0


@pytest.mark.asyncio
async def test_fire_case_under_ceiling_does_not_short_circuit(monkeypatch):
    cache = _Cache(under=True)                 # under the cap
    monkeypatch.setattr("services.enrichment_cache.get_cache", lambda: cache)
    monkeypatch.setattr("services.dnc_service.verdict", lambda phone: "callable")
    monkeypatch.setattr("services.call_window.in_call_window", lambda state, now_utc=None: True)

    async def fake_trigger(ec):
        return "call-123"
    monkeypatch.setattr("services.bland_service.trigger_voicemail", fake_trigger)
    async def fake_set_bland_status(*a, **k):
        return None
    monkeypatch.setattr("services.dedup_service.set_bland_status", fake_set_bland_status)

    res = await fire_service.fire_case(_fake_sb(), "CN1")

    assert res["status"] == "fired"
    assert cache.incremented == 1             # counted toward the daily Bland cap
