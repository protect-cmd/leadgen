"""Tests for services.quota_service (mock-backed; no live DB)."""
from __future__ import annotations

import asyncio

import pytest

from pipeline.contract import Business


# --- a tiny chainable fake supabase client --------------------------------
class _RpcCall:
    def __init__(self, resp, exc):
        self._resp, self._exc = resp, exc

    def execute(self):
        if self._exc:
            raise self._exc
        return self._resp


class _Resp:
    def __init__(self, data=None, count=None):
        self.data, self.count = data, count


class _Chain:
    def __init__(self, rec, resp):
        self._rec, self._resp = rec, resp

    def update(self, payload):
        self._rec["update"] = payload
        return self

    def select(self, *a, **k):
        self._rec["select"] = True
        return self

    def eq(self, key, val):
        self._rec.setdefault("eq", []).append((key, val))
        return self

    def in_(self, key, val):
        self._rec.setdefault("in_", []).append((key, val))
        return self

    def execute(self):
        return self._resp


class FakeClient:
    def __init__(self, *, rpc_resp=None, rpc_exc=None, table_resp=None):
        self.rpc_calls = []
        self.table_rec = {}
        self._rpc_resp, self._rpc_exc, self._table_resp = rpc_resp, rpc_exc, table_resp

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        return _RpcCall(self._rpc_resp, self._rpc_exc)

    def table(self, name):
        self.table_rec["table"] = name
        return _Chain(self.table_rec, self._table_resp)


@pytest.fixture
def qs(monkeypatch):
    import services.quota_service as qs
    # Neutralize the weekend pause so reservation tests are calendar-independent;
    # a dedicated test re-enables it.
    import services.budget_schedule as bs
    monkeypatch.setattr(bs, "paid_actions_paused", lambda *a, **k: False)
    return qs


# --- cap_for ---------------------------------------------------------------
def test_cap_for_searchbug_uses_budget_schedule(qs, monkeypatch):
    monkeypatch.delenv("QUOTA_CAP_VANTAGE_SEARCHBUG", raising=False)
    monkeypatch.delenv("QUOTA_CAP_SEARCHBUG", raising=False)
    import services.budget_schedule as bs
    monkeypatch.setattr(bs, "enrichment_cap", lambda day=None: 99)
    # no env override -> searchbug cap comes from the calendar budget
    assert qs.cap_for(Business.VANTAGE, "searchbug") == 99
    # a non-searchbug action with no override falls back to the global default
    assert qs.cap_for(Business.VANTAGE, "bland") == qs._GLOBAL_DEFAULT_CAP


def test_cap_for_resolution_order(qs, monkeypatch):
    monkeypatch.delenv("QUOTA_CAP_VANTAGE_SEARCHBUG", raising=False)
    monkeypatch.delenv("QUOTA_CAP_SEARCHBUG", raising=False)
    # per-action override
    monkeypatch.setenv("QUOTA_CAP_SEARCHBUG", "50")
    assert qs.cap_for(Business.VANTAGE, "searchbug") == 50
    # per-business+action is most specific
    monkeypatch.setenv("QUOTA_CAP_VANTAGE_SEARCHBUG", "20")
    assert qs.cap_for(Business.VANTAGE, "searchbug") == 20
    # a different business still uses the per-action value
    assert qs.cap_for(Business.ISTS, "searchbug") == 50


def test_try_reserve_denied_on_weekend_pause(qs, monkeypatch):
    import services.budget_schedule as bs
    monkeypatch.setattr(bs, "paid_actions_paused", lambda *a, **k: True)
    fake = FakeClient(rpc_resp=_Resp(data=[{"granted": True, "used": 0, "remaining": 9}]))
    monkeypatch.setattr(qs, "_client", fake)
    res = asyncio.run(qs.try_reserve(Business.VANTAGE, "searchbug", "x"))
    assert res.granted is False
    assert fake.rpc_calls == []   # never even hit the DB on a paused day


# --- try_reserve -----------------------------------------------------------
def test_try_reserve_granted_parses_and_calls_rpc(qs, monkeypatch):
    fake = FakeClient(rpc_resp=_Resp(data=[{"granted": True, "used": 5, "remaining": 95}]))
    monkeypatch.setattr(qs, "_client", fake)
    res = asyncio.run(qs.try_reserve(Business.COSNER, "searchbug", "CD-1", cap=100, day="2026-06-28"))
    assert (res.granted, res.used, res.remaining) == (True, 5, 95)
    name, params = fake.rpc_calls[0]
    assert name == "quota_try_reserve"
    assert params["p_business"] == "cosner" and params["p_action"] == "searchbug"
    assert params["p_lead_key"] == "CD-1" and params["p_cap"] == 100


def test_try_reserve_denied_when_cap_reached(qs, monkeypatch):
    fake = FakeClient(rpc_resp=_Resp(data=[{"granted": False, "used": 100, "remaining": 0}]))
    monkeypatch.setattr(qs, "_client", fake)
    res = asyncio.run(qs.try_reserve(Business.VANTAGE, "searchbug", "x", cap=100))
    assert res.granted is False and res.remaining == 0


def test_try_reserve_fails_closed_on_backend_error(qs, monkeypatch):
    fake = FakeClient(rpc_exc=RuntimeError("db down"))
    monkeypatch.setattr(qs, "_client", fake)
    res = asyncio.run(qs.try_reserve(Business.GARNISH_PROOF, "bland", "g1"))
    # The whole point: a broken quota backend must NOT allow spend.
    assert res.granted is False


# --- commit / rollback -----------------------------------------------------
def test_commit_updates_status_committed(qs, monkeypatch):
    fake = FakeClient(table_resp=_Resp(data=[]))
    monkeypatch.setattr(qs, "_client", fake)
    asyncio.run(qs.commit(Business.ISTS, "ghl", "J-1", day="2026-06-28"))
    assert fake.table_rec["table"] == "quota_ledger"
    assert fake.table_rec["update"]["status"] == "committed"
    eqs = dict(fake.table_rec["eq"])
    assert eqs["business"] == "ists" and eqs["lead_key"] == "J-1"
    assert eqs["status"] == "reserved"  # only flips a reserved row


def test_rollback_updates_status_rolled_back(qs, monkeypatch):
    fake = FakeClient(table_resp=_Resp(data=[]))
    monkeypatch.setattr(qs, "_client", fake)
    asyncio.run(qs.rollback(Business.VANTAGE, "searchbug", "v1", day="2026-06-28"))
    assert fake.table_rec["update"]["status"] == "rolled_back"
