"""Tests for the /ops page route and the /api/ops aggregation endpoint."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.main import app

client = TestClient(app)


def test_ops_serves_html():
    """GET /ops returns ops.html (status 200, has the dashboard marker)."""
    r = client.get("/ops")
    assert r.status_code == 200
    assert "ops" in r.text.lower()


def test_api_ops_returns_sections():
    """GET /api/ops returns the composed ops payload as JSON."""
    fake = {
        "as_of": "2026-06-15T12:00:00Z",
        "health": {"flags": [{"level": "ok", "msg": "All systems nominal"}]},
        "scrapes": {"rows": []},
        "spend": {"bland_today": 0, "bland_cap": 100},
        "funnel": {"vantage": {"stages": [], "outcomes": {"fired": 0, "staged": 0}},
                   "ists": {"stages": [], "outcomes": {"fired": 0, "staged": 0}}},
        "trend": {"filings": [], "phones": [], "fired": [], "days": []},
    }
    with patch("services.ops_stats.get_ops_stats", return_value=fake):
        r = client.get("/api/ops")
    assert r.status_code == 200
    body = r.json()
    assert body["as_of"] == "2026-06-15T12:00:00Z"
    assert body["health"]["flags"][0]["level"] == "ok"
    assert "funnel" in body and "spend" in body
