import asyncio
from types import SimpleNamespace


def test_enrich_batch_persists_rent_estimate(monkeypatch):
    from services import ists_enrich, rent_estimate_service

    updates = []
    records = [
        {
            "case_number": "ISTS1",
            "defendant_name": "Ifeanyi Nwankwo",
            "property_address": "123 Main St, Houston, TX 77002",
            "state": "TX",
            "county": "Harris",
        }
    ]

    class _Query:
        def __init__(self):
            self.payload = None

        def select(self, *args, **kwargs):
            return self

        def is_(self, *args, **kwargs):
            return self

        def gte(self, *args, **kwargs):
            return self

        def limit(self, *args, **kwargs):
            return self

        def update(self, payload):
            self.payload = payload
            return self

        def eq(self, *args, **kwargs):
            return self

        def execute(self):
            if self.payload is not None:
                updates.append(self.payload)
                return SimpleNamespace(data=[])
            return SimpleNamespace(data=records)

    class _Client:
        def table(self, name):
            assert name == "ists_judgments"
            return _Query()

    async def _search(**kwargs):
        return SimpleNamespace(status="phone_found", phone="7135551212")

    async def _rent(filing):
        assert filing.case_number == "ISTS1"
        assert filing.tenant_name == "Ifeanyi Nwankwo"
        assert filing.property_address == "123 Main St, Houston, TX 77002"
        return 2100.0

    monkeypatch.setattr(ists_enrich, "_client", _Client())
    monkeypatch.setattr(ists_enrich, "search_tenant_detailed", _search)
    monkeypatch.setattr(rent_estimate_service, "estimate_rent", _rent)

    result = asyncio.run(ists_enrich.enrich_batch(limit=1))

    assert result["phone_found"] == 1
    assert updates[0]["phone"] == "7135551212"
    assert updates[0]["estimated_rent"] == 2100.0
