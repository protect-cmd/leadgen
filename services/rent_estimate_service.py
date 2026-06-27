from __future__ import annotations

import csv
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from models.filing import Filing
from pipeline.qualification import extract_property_zip

log = logging.getLogger(__name__)

RENTOMETER_SUMMARY_URL = "https://www.rentometer.com/api/v1/summary"

# National ZIP-level HUD Small Area Fair Market Rents (free, no rate limit).
# Regenerate annually with scripts/build_hud_safmr_table.py.
DEFAULT_SAFMR_PATH = Path(__file__).resolve().parent.parent / "resources" / "hud_safmr_fy2026.csv"

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
    if provider == "rentometer":
        return await _estimate_rentometer(filing)
    if provider in {"hud", "safmr", "hud_safmr"}:
        return _estimate_hud_safmr(filing)

    log.warning("Rent precheck provider %r is not supported; continuing without precheck", provider)
    return None


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


@lru_cache(maxsize=4)
def _load_safmr_table(path: str) -> dict[str, dict[int, float]]:
    """ZIP -> {bedrooms: rent} from a HUD SAFMR CSV (columns zip,br0..br4)."""
    table: dict[str, dict[int, float]] = {}
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            zip_code = (row.get("zip") or "").strip().zfill(5)
            if not zip_code:
                continue
            rents: dict[int, float] = {}
            for bedrooms in range(5):
                raw = (row.get(f"br{bedrooms}") or "").strip()
                if raw:
                    try:
                        rents[bedrooms] = float(raw)
                    except ValueError:
                        continue
            if rents:
                table[zip_code] = rents
    return table


def _estimate_hud_safmr(filing: Filing) -> float | None:
    zip_code = extract_property_zip(filing.property_address)
    if not zip_code:
        return None

    path = os.getenv("HUD_SAFMR_DATA_PATH", "").strip() or str(DEFAULT_SAFMR_PATH)
    try:
        table = _load_safmr_table(path)
    except FileNotFoundError:
        log.warning("HUD SAFMR data file not found at %s; continuing without precheck", path)
        return None

    rents = table.get(zip_code)
    if not rents:
        return None

    bedrooms_raw = os.getenv("HUD_SAFMR_BEDROOMS") or os.getenv("RENTOMETER_BEDROOMS") or "2"
    try:
        bedrooms = int(bedrooms_raw)
    except ValueError:
        bedrooms = 2

    return rents.get(bedrooms) or rents.get(2)
