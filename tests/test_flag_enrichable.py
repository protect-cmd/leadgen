"""Tests for flag_enrichable's self-healing diff write."""
from __future__ import annotations

from scripts import flag_enrichable as fe


class FakeUpdateQuery:
    def __init__(self, sink):
        self._sink = sink

    def update(self, payload):
        self._value = payload["is_enrichable"]
        return self

    def in_(self, col, cases):
        self._sink.append((self._value, list(cases)))
        return self

    def execute(self):
        return None


class FakeSelectQuery:
    def __init__(self, rows):
        self._rows = rows

    def select(self, cols):
        return self

    def is_(self, col, val):  # only_null path; unused here
        return self

    def range(self, lo, hi):
        self._lo, self._hi = lo, hi
        return self

    def execute(self):
        class R:
            pass
        r = R()
        r.data = self._rows if self._lo == 0 else []
        return r


class FakeSB:
    def __init__(self, rows, sink):
        self._rows = rows
        self._sink = sink

    def table(self, name):
        # First call in flag() is the select scan; subsequent are bulk updates.
        if not getattr(self, "_scanned", False):
            self._scanned = True
            return FakeSelectQuery(self._rows)
        return FakeUpdateQuery(self._sink)


def _row(cn, bucket, enrich, name="Jane Tester", addr="12 Oak St, West Chester, OH 45069"):
    return {"case_number": cn, "lead_bucket": bucket, "tenant_name": name,
            "property_address": addr, "is_enrichable": enrich}


def test_only_writes_changed_rows(monkeypatch):
    rows = [
        _row("NEW", "residential_approved", None),     # NULL -> True  (new insert)
        _row("STUCK", "residential_approved", False),  # stale FALSE -> True (self-heal)
        _row("OK", "residential_approved", True),      # already True -> unchanged
        _row("BAD", "discarded", False),               # correctly False -> unchanged
    ]
    sink: list = []
    monkeypatch.setattr(fe, "_client", lambda: FakeSB(rows, sink))

    res = fe.flag()

    assert res == {"true": 2, "false": 0, "unchanged": 2, "total": 4}
    # Only the two changed rows were written, both set to True.
    assert len(sink) == 1
    value, cases = sink[0]
    assert value is True
    assert sorted(cases) == ["NEW", "STUCK"]
