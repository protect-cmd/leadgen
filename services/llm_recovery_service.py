"""LLM-backed recovery for filings rejected by regex gates (gate_name, gate_address).

Hosted via OpenRouter, defaults to Qwen2.5-7B-Instruct for cost. Fires ONLY
on regex-rejected leads — healthy leads bypass the LLM entirely. Returns a
structured RecoveryResult the runner can use to either re-attempt enrichment
with cleaned fields or accept the rejection.

Failure modes (timeout / HTTP error / malformed JSON / low confidence) all
collapse to ``RecoveryResult(confidence=0.0)`` so the caller never silently
approves a lead the LLM couldn't vouch for.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "qwen/qwen-2.5-7b-instruct"
DEFAULT_TIMEOUT = 10.0
RECOVERY_CONFIDENCE_THRESHOLD = 0.7


@dataclass(frozen=True)
class RecoveryResult:
    """Structured LLM verdict on a regex-rejected filing.

    confidence is on [0.0, 1.0]. Callers should treat anything below
    RECOVERY_CONFIDENCE_THRESHOLD as "stay rejected".
    """
    first: str = ""
    last: str = ""
    street: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""
    confidence: float = 0.0
    skip_reason: str | None = None

    @property
    def formatted_address(self) -> str:
        parts = [self.street, self.city, f"{self.state} {self.zip}".strip()]
        return ", ".join(p for p in parts if p)

    @property
    def formatted_name(self) -> str:
        return f"{self.first} {self.last}".strip()


def is_enabled() -> bool:
    return os.environ.get("LLM_RECOVERY_ENABLED", "false").lower() == "true"


_SYSTEM_PROMPT = (
    "You are a data-quality assistant for an eviction-filing pipeline. Court "
    "records arrive with messy tenant names and property addresses. Your job is "
    "to clean them so they can be queried against a people-search API.\n\n"
    "RULES:\n"
    "1. Never invent data. If a field is missing or unrecoverable, leave it empty.\n"
    "2. Strip occupant boilerplate ('and all other occupants', 'et al', 'AKA <other name>').\n"
    "3. Reject placeholder names (John Doe, Unknown Tenant, Squatter, etc.) — "
    "set confidence to 0.0 and explain in skip_reason.\n"
    "4. Reject business/entity names (LLC, INC, CORP, Trust, Bank, Estate of, DBA, c/o) — "
    "set confidence to 0.0 and explain in skip_reason.\n"
    "5. For names like 'LAST, FIRST MIDDLE' return first/last cleanly.\n"
    "6. For compound surnames (De La Cruz, Van Buren), keep the particles with the last name.\n"
    "7. For addresses, return street WITHOUT unit/apt suffixes — extract them out.\n"
    "8. confidence reflects how certain you are the cleaned fields will yield a real person "
    "match. Use 0.9+ only when name+street+zip are all clean and unambiguous.\n\n"
    "Respond with ONLY a JSON object, no prose, no markdown fences."
)


def _user_prompt(raw_name: str, raw_address: str, state: str) -> str:
    return (
        f"Raw tenant name: {raw_name!r}\n"
        f"Raw property address: {raw_address!r}\n"
        f"State (authoritative): {state!r}\n\n"
        "Return JSON with keys: first, last, street, city, state, zip, "
        "confidence (0.0-1.0 float), skip_reason (string or null)."
    )


_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _parse_response(content: str) -> RecoveryResult:
    """Extract RecoveryResult from raw LLM content. Returns zero-confidence on
    any parse failure — caller treats that as 'stay rejected'."""
    cleaned = _JSON_FENCE_RE.sub("", content or "").strip()
    if not cleaned:
        return RecoveryResult()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        log.warning("LLM recovery: invalid JSON in response: %r", content[:200])
        return RecoveryResult()
    if not isinstance(data, dict):
        return RecoveryResult()
    try:
        confidence = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return RecoveryResult(
        first=str(data.get("first") or "").strip(),
        last=str(data.get("last") or "").strip(),
        street=str(data.get("street") or "").strip(),
        city=str(data.get("city") or "").strip(),
        state=str(data.get("state") or "").strip().upper(),
        zip=str(data.get("zip") or "").strip(),
        confidence=max(0.0, min(1.0, confidence)),
        skip_reason=(data.get("skip_reason") or None) or None,
    )


async def recover(raw_name: str, raw_address: str, state: str) -> RecoveryResult:
    """Ask the LLM to clean a regex-rejected (name, address) pair.

    Returns a zero-confidence RecoveryResult on any failure so the caller can
    cleanly treat it as 'stay rejected'. The LLM never silently approves.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        log.warning("LLM recovery requested but OPENROUTER_API_KEY is not set")
        return RecoveryResult()

    model = os.environ.get("LLM_RECOVERY_MODEL", DEFAULT_MODEL)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _user_prompt(raw_name, raw_address, state)},
        ],
        "temperature": 0.0,
        "max_tokens": 300,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.post(OPENROUTER_URL, json=payload, headers=headers)
    except (httpx.TimeoutException, httpx.HTTPError) as e:
        log.warning("LLM recovery HTTP failure: %s", e)
        return RecoveryResult()

    if response.status_code != 200:
        log.warning(
            "LLM recovery non-200 (%s): %s",
            response.status_code, response.text[:200],
        )
        return RecoveryResult()

    try:
        body = response.json()
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as e:
        log.warning("LLM recovery response shape unexpected: %s", e)
        return RecoveryResult()

    return _parse_response(content)
