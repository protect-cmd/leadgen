"""HTTP Basic Auth enforcement for the dashboard."""
from __future__ import annotations

import base64
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.main import app

client = TestClient(app)

SEARCH_PW = "search-secret"
QUEUE_PW = "queue-secret"


def _auth(pw: str) -> dict:
    token = base64.b64encode(f"user:{pw}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def enforce_auth(monkeypatch):
    monkeypatch.setenv("DASHBOARD_SEARCH_PASSWORD", SEARCH_PW)
    monkeypatch.setenv("DASHBOARD_QUEUE_PASSWORD", QUEUE_PW)


def test_root_unauthenticated_returns_401(enforce_auth):
    r = client.get("/")
    assert r.status_code == 401
    assert "Basic" in r.headers.get("WWW-Authenticate", "")


def test_root_with_search_password_returns_200(enforce_auth):
    r = client.get("/", headers=_auth(SEARCH_PW))
    assert r.status_code == 200


def test_root_with_queue_password_returns_401(enforce_auth):
    """Queue password must NOT unlock the search page."""
    r = client.get("/", headers=_auth(QUEUE_PW))
    assert r.status_code == 401


def test_queue_unauthenticated_returns_401(enforce_auth):
    r = client.get("/queue")
    assert r.status_code == 401


def test_queue_with_queue_password_returns_200(enforce_auth):
    r = client.get("/queue", headers=_auth(QUEUE_PW))
    assert r.status_code == 200


def test_queue_with_search_password_returns_401(enforce_auth):
    """Search password must NOT unlock the queue page."""
    r = client.get("/queue", headers=_auth(SEARCH_PW))
    assert r.status_code == 401


def test_api_search_requires_search_password(enforce_auth):
    with patch("dashboard.main.search_leads", new_callable=AsyncMock, return_value=[]):
        r = client.get("/api/search?q=x", headers=_auth(SEARCH_PW))
        assert r.status_code == 200
        r = client.get("/api/search?q=x", headers=_auth(QUEUE_PW))
        assert r.status_code == 401


def test_api_leads_requires_queue_password(enforce_auth):
    with patch("dashboard.main.get_dashboard_leads", new_callable=AsyncMock, return_value=[]):
        r = client.get("/api/leads?view=residential_approved", headers=_auth(QUEUE_PW))
        assert r.status_code == 200
        r = client.get("/api/leads?view=residential_approved", headers=_auth(SEARCH_PW))
        assert r.status_code == 401


def test_api_skip_accepts_either_password(enforce_auth):
    """Skip is shared between dashboards — either password should unlock."""
    with patch("dashboard.main.get_pending_leads", new_callable=AsyncMock, return_value=[]):
        # The endpoint will return 404 because no lead matches — but that's
        # AFTER auth passes, which is what we care about here.
        r1 = client.post("/api/leads/X/skip?track=ec", headers=_auth(SEARCH_PW))
        r2 = client.post("/api/leads/X/skip?track=ec", headers=_auth(QUEUE_PW))
        assert r1.status_code != 401
        assert r2.status_code != 401


def test_no_passwords_set_means_open_mode(monkeypatch):
    """If neither env var is set, all routes are open (test/dev default)."""
    monkeypatch.delenv("DASHBOARD_SEARCH_PASSWORD", raising=False)
    monkeypatch.delenv("DASHBOARD_QUEUE_PASSWORD", raising=False)
    r = client.get("/")
    assert r.status_code == 200
    r = client.get("/queue")
    assert r.status_code == 200
