from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger(__name__)

_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"


async def normalize_address(raw: str) -> str | None:
    """
    Validate and normalize a raw address string via Google Geocoding API.
    Returns the formatted_address string on success, None on failure or if
    no key is configured (caller falls back to the original address).
    """
    if not raw or raw.strip().lower() in {"unknown", ""}:
        return None

    key = os.environ.get("GOOGLE_GEOCODING_API_KEY", "")
    if not key:
        return None

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                _GEOCODE_URL,
                params={"address": raw, "key": key},
            )
        if r.status_code != 200:
            log.warning(f"Geocode HTTP {r.status_code} for: {raw[:60]}")
            return None

        data = r.json()
        if data.get("status") != "OK":
            log.debug(f"Geocode status={data.get('status')} for: {raw[:60]}")
            return None

        return data["results"][0]["formatted_address"]

    except Exception as e:
        log.warning(f"Geocode failed for '{raw[:60]}': {e}")
        return None
