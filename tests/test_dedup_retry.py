from __future__ import annotations

import httpx

from services import dedup_service


class _FlakyQuery:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self):
        self.calls += 1
        if self.calls == 1:
            raise httpx.RemoteProtocolError("server disconnected")
        return "ok"


def test_supabase_execute_retries_transient_transport_errors(monkeypatch):
    query = _FlakyQuery()
    monkeypatch.setattr(dedup_service.time, "sleep", lambda _seconds: None)

    assert dedup_service._execute_with_retry(query, "test write") == "ok"
    assert query.calls == 2
