"""Phase 9: multi-business freshness + Pushover health notifier."""
from __future__ import annotations

import asyncio
import types
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock

import scripts.verify_pipeline_health as vph
from scripts.verify_pipeline_health import CheckResult


class _Chain:
    def __init__(self, data):
        self._data = data

    def select(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def execute(self):
        return types.SimpleNamespace(data=self._data, count=len(self._data))


class _FakeClient:
    def __init__(self, by_table):
        self._by_table = by_table

    def table(self, name):
        return _Chain(self._by_table.get(name, []))


def _status(results, name):
    return next(r.status for r in results if r.name == name)


def test_business_freshness_recent_all_ok(monkeypatch):
    today = date.today().isoformat()
    monkeypatch.setattr(vph, "_supabase_client", lambda: _FakeClient({
        "ists_judgments": [{"judgment_date": today}],
        "cosner_filings": [{"filing_date": today}],
        "garnishment_orders": [{"filing_date": today}],
    }))
    res = vph.check_business_table_freshness(datetime.now(timezone.utc))
    assert _status(res, "ISTS freshness") == "OK"
    assert _status(res, "Cosner Drake freshness") == "OK"


def test_business_freshness_stale_scheduled_fails_but_manual_ok(monkeypatch):
    old = (date.today() - timedelta(days=12)).isoformat()
    monkeypatch.setattr(vph, "_supabase_client", lambda: _FakeClient({
        "ists_judgments": [{"judgment_date": old}],
        "cosner_filings": [{"filing_date": old}],
        "garnishment_orders": [{"filing_date": old}],
    }))
    res = vph.check_business_table_freshness(datetime.now(timezone.utc))
    assert _status(res, "ISTS freshness") == "FAIL"          # scheduled + dark
    assert _status(res, "Cosner Drake freshness") == "FAIL"
    # GP is a manual import — stale is expected, never alerted
    assert _status(res, "Garnish Proof (manual import) freshness") == "OK"


def test_quota_budget_is_never_a_fail(monkeypatch):
    monkeypatch.setattr(vph, "_supabase_client", lambda: _FakeClient({
        "quota_ledger": [
            {"business": "vantage", "action": "searchbug"},
            {"business": "vantage", "action": "searchbug"},
            {"business": "ists", "action": "bland"},
        ],
    }))
    res = vph.check_quota_budget(datetime.now(timezone.utc))
    assert all(r.status == "OK" for r in res)               # budget-limit, not FAIL
    assert any("2 used today" in r.detail for r in res)


def test_notify_health_pushes_summary_and_returns_fail_count(monkeypatch):
    monkeypatch.setattr(vph, "gather_results", lambda: [
        CheckResult("env", "X", "OK", "ok"),
        CheckResult("scrapers", "ISTS freshness", "FAIL", "dark"),
        CheckResult("schema", "Y", "FLAG", "stale col"),
    ])
    sent = {}
    async def _fake_send(title, body, tags=None):
        sent["title"] = title; sent["body"] = body
    import services.notification_service as ns
    monkeypatch.setattr(ns, "send_alert", _fake_send)

    fails = asyncio.run(vph.notify_health())

    assert fails == 1
    assert "1 OK / 1 FLAG / 1 FAIL" in sent["title"]
    assert "[FAIL] ISTS freshness" in sent["body"]          # FAILs surfaced first
