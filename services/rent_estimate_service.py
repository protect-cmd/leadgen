from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from models.filing import Filing

log = logging.getLogger(__name__)

RENTOMETER_SUMMARY_URL = "https://www.rentometer.com/api/v1/summary"

# /summary only accepts these values (per Rentometer API docs). Anything else
# makes the endpoint reject the request, so we validate before spending a call.
_VALID_BEDROOMS = {"1", "2", "3", "4"}
_VALID_BATHS = {"1", "1.5+"}
_VALID_BUILDING_TYPES = {"apartment", "house"}


def _valid_bedrooms(raw: str | None) -> str:
    value = (raw or "").strip()
    if value in _VALID_BEDROOMS:
        return value
    if value:
        log.warning(
            "RENTOMETER_BEDROOMS=%r is not one of %s; falling back to 2",
            value, sorted(_VALID_BEDROOMS),
        )
    return "2"


def _valid_choice(name: str, raw: str | None, allowed: set[str]) -> str:
    value = (raw or "").strip()
    if not value or value in allowed:
        return value
    log.warning("%s=%r is not one of %s; dropping it", name, value, sorted(allowed))
    return ""


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def is_enabled() -> bool:
    return _truthy(os.getenv("RENT_PRECHECK_ENABLED"))


async def estimate_rent(filing: Filing) -> float | None:
    if not is_enabled():
        return None

    provider = os.getenv("RENT_PRECHECK_PROVIDER", "rentometer").strip().lower()
    if provider != "rentometer":
        log.warning("Rent precheck provider %r is not supported; continuing without precheck", provider)
        return None

    return await _estimate_rentometer(filing)


async def _estimate_rentometer(filing: Filing) -> float | None:
    api_key = os.getenv("RENTOMETER_API_KEY", "").strip()
    if not api_key:
        log.info("Rent precheck enabled but RENTOMETER_API_KEY is not set; continuing")
        return None

    params = {
        "api_key": api_key,
        "address": filing.property_address,
        "bedrooms": _valid_bedrooms(os.getenv("RENTOMETER_BEDROOMS", "2")),
    }

    # Only forward optional params whose values the /summary endpoint accepts.
    # An invalid value (e.g. building_type=apartments) makes Rentometer reject
    # the whole query, so we drop+warn rather than burn the call on a 4xx.
    baths = _valid_choice("RENTOMETER_BATHS", os.getenv("RENTOMETER_BATHS", ""), _VALID_BATHS)
    building_type = _valid_choice(
        "RENTOMETER_BUILDING_TYPE", os.getenv("RENTOMETER_BUILDING_TYPE", ""), _VALID_BUILDING_TYPES
    )
    look_back_days = os.getenv("RENTOMETER_LOOK_BACK_DAYS", "").strip()
    optional_params = {
        "baths": baths,
        "building_type": building_type,
        "look_back_days": look_back_days,
    }
    params.update({key: value for key, value in optional_params.items() if value})

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(RENTOMETER_SUMMARY_URL, params=params)
    except Exception as exc:
        log.warning("Rentometer precheck failed for %s: %s", filing.case_number, exc)
        return None

    if response.status_code != 200:
        log.warning(
            "Rentometer precheck returned %s for %s: %s",
            response.status_code,
            filing.case_number,
            response.text[:200],
        )
        return None

    try:
        payload = response.json()
    except ValueError as exc:
        log.warning("Rentometer precheck returned invalid JSON for %s: %s", filing.case_number, exc)
        return None

    return _rent_from_response(payload)


def _rent_from_response(payload: dict[str, Any]) -> float | None:
    for key in ("median", "mean"):
        value = payload.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None
