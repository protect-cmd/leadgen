"""Unit tests for the free HUD SAFMR rent backfill helpers."""
from __future__ import annotations

from scripts import backfill_rent_hud as hud


def test_rent_for_zip_uses_2br_and_pads():
    table = {"45069": {1: 1500.0, 2: 1880.0, 3: 2400.0}}
    assert hud._rent_for_zip(table, "45069") == 1880.0
    # zero-padding: a 4-digit zip should still match the 5-digit key
    assert hud._rent_for_zip(table, "5069") is None  # different zip, no match
    assert hud._rent_for_zip(table, "00000") is None


def test_zip_for_prefers_property_zip_then_address():
    assert hud._zip_for({"property_zip": "45011"}) == "45011"
    assert hud._zip_for(
        {"property_zip": None, "property_address": "7 Main St, West Chester, OH 45069"}
    ) == "45069"
    assert hud._zip_for({"property_zip": "", "property_address": "no zip here"}) is None


def test_backfill_only_fills_and_groups(monkeypatch):
    """backfill() should map each null-rent row to its ZIP's 2BR rent, skip
    rows with no zip / no SAFMR match, and never touch non-null rows."""
    monkeypatch.setattr(hud, "_safmr_table", lambda: {"45069": {2: 1880.0}, "44035": {2: 1150.0}})

    fetched = [
        {"case_number": "A", "property_zip": "45069", "property_address": ""},
        {"case_number": "B", "property_zip": "44035", "property_address": ""},
        {"case_number": "C", "property_zip": "99999", "property_address": ""},  # no SAFMR
        {"case_number": "D", "property_zip": "", "property_address": "no zip"},  # no zip
    ]
    monkeypatch.setattr(hud, "_fetch_null_rent", lambda sb, t, c: fetched)

    writes: list[tuple[float, list[str]]] = []

    class FakeQuery:
        def update(self, payload):
            self._rent = payload["estimated_rent"]
            return self

        def in_(self, col, cases):
            writes.append((self._rent, list(cases)))
            return self

        def execute(self):
            return None

    class FakeSB:
        def table(self, name):
            return FakeQuery()

    res = hud.backfill(FakeSB(), "filings", "cols", write=True)
    assert res == {"table": "filings", "null_rows": 4, "matched": 2,
                   "no_zip": 1, "no_safmr_match": 1}
    assert sorted(writes) == [(1150.0, ["B"]), (1880.0, ["A"])]
