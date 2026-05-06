from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from models.filing import Filing

log = logging.getLogger(__name__)

RENTOMETER_SUMMARY_URL = "https://www.rentometer.com/api/v1/summary"


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
        "bedrooms": os.getenv("RENTOMETER_BEDROOMS", "2").strip() or "2",
    }

    optional_params = {
        "baths": os.getenv("RENTOMETER_BATHS", "").strip(),
        "building_type": os.getenv("RENTOMETER_BUILDING_TYPE", "").strip(),
        "look_back_days": os.getenv("RENTOMETER_LOOK_BACK_DAYS", "").strip(),
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
