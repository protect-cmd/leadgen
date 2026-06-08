import asyncio
from datetime import date
import services.ists_prior_work as pw
from models.judgment import JudgmentRecord


class _Resp:
    def __init__(self, data): self.data = data


class _Q:
    def __init__(self, data): self._data = data
    def select(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def execute(self): return _Resp(self._data)


class _FakeClient:
    def __init__(self, lead_rows): self._lead_rows = lead_rows
    def table(self, name):
        return _Q(self._lead_rows if name == "lead_contacts" else [])


def test_annotate_sets_prior_phone_and_bland(monkeypatch):
    fake = _FakeClient([
        {"case_number": "A1", "phone": "7135550101", "bland_status": "triggered"},
    ])
    monkeypatch.setattr(pw, "_client", fake)
    recs = [
        JudgmentRecord("A1", "Jo Lee", "1 Main St, Houston, TX 77002",
                       judgment_date=date(2026, 6, 1), judgment_against="Lee"),
        JudgmentRecord("B2", "Sam Fox", "2 Oak St, Houston, TX 77003",
                       judgment_date=date(2026, 6, 1), judgment_against="Fox"),
    ]
    out = asyncio.run(pw.annotate_prior_work(recs))
    by = {r.case_number: r for r in out}
    assert by["A1"].prior_phone is True and by["A1"].prior_bland_status == "triggered"
    assert by["B2"].prior_phone is False and by["B2"].prior_bland_status is None
