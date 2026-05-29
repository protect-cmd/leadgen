"""Tests for routing changes + new search/note/mark-called endpoints (Spec 4)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.main import app

client = TestClient(app)


def test_root_serves_search_html():
    """GET / returns search.html (status 200, contains the search input id)."""
    r = client.get("/")
    assert r.status_code == 200
    assert "search-input" in r.text or "search" in r.text.lower()


def test_queue_serves_legacy_index_html():
    """GET /queue serves the original index.html (status 200, has brand chips)."""
    r = client.get("/queue")
    assert r.status_code == 200
    assert "VANTAGE" in r.text or "GRANT" in r.text


def test_search_endpoint_returns_results():
    fake_rows = [
        {"case_number": "C-1", "tenant_name": "Maria",
         "property_address": "1 Main", "filing_date": "2026-05-29",
         "state": "TX", "county": "Harris", "phone": "5551234567"},
    ]
    with patch("dashboard.main.search_leads", new_callable=AsyncMock, return_value=fake_rows):
        r = client.get("/api/search?q=maria")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert body[0]["case_number"] == "C-1"


def test_search_endpoint_short_query_returns_empty():
    """q under 2 chars returns 200 with empty list (no error)."""
    with patch("dashboard.main.search_leads", new_callable=AsyncMock, return_value=[]) as mock_search:
        r = client.get("/api/search?q=a")
    assert r.status_code == 200
    assert r.json() == []
    mock_search.assert_not_called()


def test_search_endpoint_missing_q_returns_empty():
    """Missing q returns 200 with empty list."""
    with patch("dashboard.main.search_leads", new_callable=AsyncMock, return_value=[]):
        r = client.get("/api/search")
    assert r.status_code == 200
    assert r.json() == []


def test_note_endpoint_inserts_note():
    fake_row = {"id": 1, "case_number": "C-1", "track": "ng",
                "note_text": "vm left", "author": "caller",
                "created_at": "2026-05-29T20:00:00+00:00"}
    with patch("dashboard.main.add_lead_note", new_callable=AsyncMock, return_value=fake_row):
        r = client.post("/api/leads/C-1/note?track=ng", json={"text": "vm left"})
    assert r.status_code == 200
    assert r.json()["id"] == 1


def test_note_endpoint_rejects_empty_text():
    """Empty note text returns 400."""
    async def _raise(**_):
        raise ValueError("note text is empty")
    with patch("dashboard.main.add_lead_note", new=_raise):
        r = client.post("/api/leads/C-1/note?track=ng", json={"text": ""})
    assert r.status_code == 400


def test_notes_list_endpoint_returns_rows():
    fake_notes = [
        {"id": 2, "note_text": "newest", "author": "caller",
         "created_at": "2026-05-29T20:00:00+00:00"},
    ]
    with patch("dashboard.main.list_lead_notes", new_callable=AsyncMock, return_value=fake_notes):
        r = client.get("/api/leads/C-1/notes?track=ng")
    assert r.status_code == 200
    assert r.json()[0]["id"] == 2


def test_mark_called_endpoint_returns_timestamp():
    with patch("dashboard.main.mark_lead_called",
               new_callable=AsyncMock,
               return_value="2026-05-29T20:00:00+00:00"):
        r = client.post("/api/leads/C-1/mark-called?track=ng")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "last_called_at" in body
