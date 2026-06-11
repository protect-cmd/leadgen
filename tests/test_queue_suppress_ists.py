"""C2 regression: cross-track dedup ('ISTS wins') must suppress a Vantage lead
whose person matches an ISTS judgment. This broke when _SELECT omitted
property_zip, so the fake here PROJECTS rows to the selected columns to catch it."""
from datetime import date

from pipeline import queue_builder


class _Resp:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, rows):
        self._rows = rows
        self._cols = None
        self._slice = (0, 999)

    def select(self, cols="*", **_k):
        self._cols = cols
        return self

    def order(self, *_a, **_k):
        return self

    def is_(self, *_a, **_k):
        return self

    def gte(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    @property
    def not_(self):
        return self

    def range(self, lo, hi):
        self._slice = (lo, hi)
        return self

    def execute(self):
        lo, hi = self._slice
        rows = self._rows[lo:hi + 1]
        if self._cols and self._cols != "*":
            keep = [c.strip() for c in self._cols.split(",")]
            rows = [{k: r.get(k) for k in keep} for r in rows]
        return _Resp(rows)


class _FakeSB:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Q(self.tables.get(name, []))


def test_build_to_enrich_suppresses_ists_person():
    sb = _FakeSB({
        "good_leads_now": [
            {"case_number": "V_MATCH", "tenant_name": "John Smith",
             "property_address": "100 Main St, Houston, TX 77002", "property_zip": "77002",
             "state": "TX", "county": "Harris", "filing_date": "2026-06-11", "court_date": None,
             "priority_rank": None, "priority_metro": None, "estimated_rent": 2000},
            {"case_number": "V_KEEP", "tenant_name": "Jane Doe",
             "property_address": "200 Oak St, Houston, TX 77004", "property_zip": "77004",
             "state": "TX", "county": "Harris", "filing_date": "2026-06-11", "court_date": None,
             "priority_rank": None, "priority_metro": None, "estimated_rent": 2000},
        ],
        "ists_judgments": [
            {"defendant_name": "Smith, John",
             "property_address": "100 Main St, Houston, TX 77002"},
        ],
    })

    rows = queue_builder.build_to_enrich(sb, "dnc_dir", today=date(2026, 6, 12))
    cns = {r["case_number"] for r in rows}

    assert "V_MATCH" not in cns      # same person+zip as the ISTS judgment -> suppressed
    assert "V_KEEP" in cns
