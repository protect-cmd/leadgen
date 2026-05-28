"""LLM recovery service — fail-closed on every error path, parses clean JSON
on the happy path. The service must NEVER silently approve a lead the LLM
couldn't vouch for."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from services import llm_recovery_service
from services.llm_recovery_service import (
    RECOVERY_CONFIDENCE_THRESHOLD,
    RecoveryResult,
    _parse_response,
    recover,
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_RECOVERY_ENABLED", "true")


def _build_openrouter_response(payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": json.dumps(payload)}}]},
    )


# ── _parse_response unit tests ────────────────────────────────────────────

def test_parse_response_clean_json():
    payload = {
        "first": "Maria", "last": "Garcia",
        "street": "123 Main St", "city": "Houston",
        "state": "TX", "zip": "77002",
        "confidence": 0.9, "skip_reason": None,
    }
    result = _parse_response(json.dumps(payload))
    assert result.first == "Maria"
    assert result.last == "Garcia"
    assert result.state == "TX"
    assert result.confidence == 0.9
    assert result.skip_reason is None


def test_parse_response_strips_json_fences():
    payload = {"first": "A", "last": "B", "confidence": 0.8}
    fenced = f"```json\n{json.dumps(payload)}\n```"
    result = _parse_response(fenced)
    assert result.first == "A"
    assert result.confidence == 0.8


def test_parse_response_invalid_json_returns_zero_confidence():
    result = _parse_response("not json at all")
    assert result.confidence == 0.0


def test_parse_response_empty_returns_zero_confidence():
    assert _parse_response("").confidence == 0.0
    assert _parse_response(None).confidence == 0.0  # type: ignore[arg-type]


def test_parse_response_clamps_confidence():
    payload = {"first": "X", "last": "Y", "confidence": 1.5}
    assert _parse_response(json.dumps(payload)).confidence == 1.0
    payload2 = {"first": "X", "last": "Y", "confidence": -0.5}
    assert _parse_response(json.dumps(payload2)).confidence == 0.0


def test_parse_response_non_dict_returns_zero():
    assert _parse_response("[1, 2, 3]").confidence == 0.0


def test_parse_response_invalid_confidence_type():
    payload = {"first": "X", "last": "Y", "confidence": "not-a-number"}
    assert _parse_response(json.dumps(payload)).confidence == 0.0


def test_formatted_helpers():
    r = RecoveryResult(
        first="John", last="Doe",
        street="1 Main St", city="Austin", state="TX", zip="78701",
        confidence=0.9,
    )
    assert r.formatted_name == "John Doe"
    assert r.formatted_address == "1 Main St, Austin, TX 78701"


# ── recover() integration tests ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_recover_happy_path():
    payload = {
        "first": "Maria", "last": "Garcia",
        "street": "123 Main St", "city": "Houston",
        "state": "TX", "zip": "77002",
        "confidence": 0.92, "skip_reason": None,
    }
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=_build_openrouter_response(payload))):
        result = await recover("garcia, maria", "123 main, houston tx", "TX")
    assert result.confidence == 0.92
    assert result.first == "Maria"
    assert result.zip == "77002"


@pytest.mark.asyncio
async def test_recover_http_error_returns_zero():
    with patch(
        "httpx.AsyncClient.post",
        new=AsyncMock(return_value=httpx.Response(500, text="server error")),
    ):
        result = await recover("x", "y", "TX")
    assert result.confidence == 0.0


@pytest.mark.asyncio
async def test_recover_timeout_returns_zero():
    with patch(
        "httpx.AsyncClient.post",
        new=AsyncMock(side_effect=httpx.TimeoutException("timeout")),
    ):
        result = await recover("x", "y", "TX")
    assert result.confidence == 0.0


@pytest.mark.asyncio
async def test_recover_missing_api_key_returns_zero(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    result = await recover("x", "y", "TX")
    assert result.confidence == 0.0


@pytest.mark.asyncio
async def test_recover_malformed_response_returns_zero():
    bad_response = httpx.Response(200, json={"unexpected": "shape"})
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=bad_response)):
        result = await recover("x", "y", "TX")
    assert result.confidence == 0.0


@pytest.mark.asyncio
async def test_recover_passes_model_from_env(monkeypatch):
    monkeypatch.setenv("LLM_RECOVERY_MODEL", "qwen/qwen-2.5-72b-instruct")
    payload = {"first": "A", "last": "B", "confidence": 0.5}
    mock_post = AsyncMock(return_value=_build_openrouter_response(payload))
    with patch("httpx.AsyncClient.post", new=mock_post):
        await recover("x", "y", "TX")
    sent_body = mock_post.call_args.kwargs["json"]
    assert sent_body["model"] == "qwen/qwen-2.5-72b-instruct"


# ── is_enabled flag ───────────────────────────────────────────────────────

def test_is_enabled_default_off(monkeypatch):
    monkeypatch.delenv("LLM_RECOVERY_ENABLED", raising=False)
    assert llm_recovery_service.is_enabled() is False


def test_is_enabled_true(monkeypatch):
    monkeypatch.setenv("LLM_RECOVERY_ENABLED", "true")
    assert llm_recovery_service.is_enabled() is True


def test_is_enabled_garbage_off(monkeypatch):
    monkeypatch.setenv("LLM_RECOVERY_ENABLED", "maybe")
    assert llm_recovery_service.is_enabled() is False
