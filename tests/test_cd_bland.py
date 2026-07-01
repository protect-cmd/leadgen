"""Cosner Drake Bland trigger (no network). Pins language routing, the
missing-agent guard, and the forward-looking Answer-window gate
(answer_deadline >= today) that distinguishes CD from ISTS/GP."""
import asyncio
from datetime import date

import services.cd_bland as b


def _rec(**kw):
    base = dict(
        case_number="261100274020",
        defendant_name="Linda D Jones",
        defendant_address="8635 Cottage Gate Ln, Houston, TX 77088",
        phone="+12815550123",
        language_hint="english_likely",
        county="Harris",
        filing_date="2026-06-24",
        answer_deadline="2026-07-24",
        ghl_contact_id="ghl_abc",
    )
    base.update(kw)
    return base


def test_dry_run_returns_marker_when_agent_set(monkeypatch):
    monkeypatch.setattr(b, "_CD_AGENT_ID", "eng-agent")
    monkeypatch.setattr(b, "_CD_PHONE_NUMBER", "+18883382915")
    # Force inside the call window so the dry-run path is reached deterministically.
    monkeypatch.setattr(b, "_in_call_window", lambda *a, **k: True)
    out = asyncio.run(b.trigger_call(_rec(), dry_run=True))
    assert out == "dry-run"


def test_spanish_hint_requires_spanish_agent(monkeypatch):
    # English agent set, Spanish agent NOT set -> spanish lead is skipped (no_agent).
    monkeypatch.setattr(b, "_CD_AGENT_ID", "eng-agent")
    monkeypatch.setattr(b, "_CD_SPANISH_AGENT_ID", "")
    monkeypatch.setattr(b, "_CD_PHONE_NUMBER", "+18883382915")
    monkeypatch.setattr(b, "_in_call_window", lambda *a, **k: True)
    out = asyncio.run(b.trigger_call(_rec(language_hint="spanish_likely"), dry_run=True))
    assert out is None


def test_missing_agent_returns_none(monkeypatch):
    monkeypatch.setattr(b, "_CD_AGENT_ID", "")
    monkeypatch.setattr(b, "_CD_PHONE_NUMBER", "+18883382915")
    out = asyncio.run(b.trigger_call(_rec(), dry_run=True))
    assert out is None


class _FakeNot:
    def __init__(self, q):
        self.q = q

    def is_(self, *a, **k):
        return self.q


class _FakeQuery:
    def __init__(self, sink):
        self.sink = sink

    def select(self, *a, **k):
        return self

    @property
    def not_(self):
        return _FakeNot(self)

    def is_(self, *a, **k):
        return self

    def gte(self, col, val):
        self.sink.append(("gte", col, val))
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return type("R", (), {"data": []})()


class _FakeClient:
    def __init__(self, sink):
        self.sink = sink

    def table(self, *a, **k):
        return _FakeQuery(self.sink)


def test_batch_gates_on_open_answer_window(monkeypatch):
    sink: list = []
    monkeypatch.setattr(b, "_client", _FakeClient(sink))
    asyncio.run(b.trigger_batch(limit=5, dry_run=True))
    # Must gate forward on answer_deadline >= today (not a backward freshness lookback).
    gte_calls = [c for c in sink if c[0] == "gte"]
    assert ("gte", "answer_deadline", date.today().isoformat()) in gte_calls
