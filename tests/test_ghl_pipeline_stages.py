"""Tests for services.ghl_service pipeline helpers added in Spec 2."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.ghl_service import list_pipelines, create_pipeline_stage


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


@pytest.mark.asyncio
async def test_create_pipeline_stage_returns_existing_id_when_name_matches():
    """Idempotency: if a stage with the same name already exists, return its
    ID without making a PUT call."""
    existing = {
        "pipelines": [{
            "id": "pip1", "name": "Main",
            "stages": [
                {"id": "stage-review", "name": "Review - SearchBug Mismatch", "position": 0},
                {"id": "stage-new", "name": "New Filing", "position": 1},
            ],
        }]
    }
    get_mock = AsyncMock(return_value=_ok(existing))
    put_mock = AsyncMock()
    with patch("httpx.AsyncClient.get", new=get_mock), \
         patch("httpx.AsyncClient.put", new=put_mock):
        sid = await create_pipeline_stage(
            track="ng",
            pipeline_id="pip1",
            name="Review - SearchBug Mismatch",
            position=0,
        )
    assert sid == "stage-review"
    put_mock.assert_not_called()


@pytest.mark.asyncio
async def test_create_pipeline_stage_creates_new_at_position():
    """Inserts the new stage at the requested position and returns the new ID."""
    existing = {
        "pipelines": [{
            "id": "pip1", "name": "Main",
            "stages": [
                {"id": "s-new", "name": "New Filing", "position": 0},
                {"id": "s-won", "name": "Won", "position": 1},
            ],
        }]
    }
    updated = {
        "pipeline": {
            "id": "pip1", "name": "Main",
            "stages": [
                {"id": "s-review-NEW", "name": "Review - SearchBug Mismatch", "position": 0},
                {"id": "s-new", "name": "New Filing", "position": 1},
                {"id": "s-won", "name": "Won", "position": 2},
            ],
        }
    }
    get_mock = AsyncMock(return_value=_ok(existing))
    put_mock = AsyncMock(return_value=_ok(updated))
    with patch("httpx.AsyncClient.get", new=get_mock), \
         patch("httpx.AsyncClient.put", new=put_mock):
        sid = await create_pipeline_stage(
            track="ng",
            pipeline_id="pip1",
            name="Review - SearchBug Mismatch",
            position=0,
        )
    assert sid == "s-review-NEW"
    put_mock.assert_called_once()
    sent_stages = put_mock.call_args.kwargs["json"]["stages"]
    assert sent_stages[0]["name"] == "Review - SearchBug Mismatch"
    assert sent_stages[1]["id"] == "s-new"


@pytest.mark.asyncio
async def test_create_pipeline_stage_raises_when_pipeline_not_found():
    payload = {"pipelines": [{"id": "other", "name": "Other", "stages": []}]}
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_ok(payload))):
        with pytest.raises(RuntimeError, match="pipeline.*pip1.*not found"):
            await create_pipeline_stage(
                track="ng", pipeline_id="pip1",
                name="Review - SearchBug Mismatch", position=0,
            )
