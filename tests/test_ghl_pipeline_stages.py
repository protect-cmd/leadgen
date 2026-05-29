"""Tests for services.ghl_service pipeline helpers added in Spec 2."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.ghl_service import list_pipelines


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("GHL_API_KEY", "test-key")
    monkeypatch.setenv("GHL_NG_LOCATION_ID", "loc-ng")
    monkeypatch.setenv("GHL_EC_LOCATION_ID", "loc-ec")


def _ok(payload):
    return httpx.Response(200, json=payload)


@pytest.mark.asyncio
async def test_list_pipelines_ng_returns_parsed_payload():
    payload = {
        "pipelines": [
            {"id": "pip1", "name": "Main", "stages": [
                {"id": "s1", "name": "New Filing", "position": 0},
            ]},
            {"id": "pip2", "name": "Other", "stages": []},
        ]
    }
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_ok(payload))) as mock_get:
        result = await list_pipelines(track="ng")
    assert len(result) == 2
    assert result[0]["id"] == "pip1"
    assert result[0]["stages"][0]["name"] == "New Filing"
    call_kwargs = mock_get.call_args.kwargs
    assert call_kwargs["params"]["locationId"] == "loc-ng"


@pytest.mark.asyncio
async def test_list_pipelines_returns_empty_on_http_error():
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=httpx.Response(500, text="boom"))):
        result = await list_pipelines(track="ng")
    assert result == []
