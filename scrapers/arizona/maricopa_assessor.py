from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import requests

ASSESSOR_QUERY_URL = (
    "https://services.arcgis.com/ykpntM6e3tHvzKRJ/arcgis/rest/services/"
    "Parcels_view/FeatureServer/0/query"
)

AddressMatchStatus = Literal["single_match", "ambiguous", "no_match", "error"]


@dataclass(frozen=True)
class ParcelRecord:
    apn: str
    owner_name: str
    physical_address: str
    mailing_address: str
    physical_city: str
    physical_zip: str
    jurisdiction: str


@dataclass(frozen=True)
class AddressMatchResult:
    status: AddressMatchStatus
    query_variant: str = ""
    records: list[ParcelRecord] | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.records is None:
            object.__setattr__(self, "records", [])


class MaricopaAssessorClient:
    """No-cost public parcel-owner matcher for Maricopa address proofing."""

    def __init__(self, session: requests.Session | None = None, result_limit: int = 25):
        self.session = session or requests.Session()
        self.result_limit = result_limit

    def match_owner(self, landlord_name: str) -> AddressMatchResult:
        try:
            for variant in _owner_search_variants(landlord_name):
                records = self._query_owner(variant)
                if not records:
                    continue
                status: AddressMatchStatus = "single_match" if len(records) == 1 else "ambiguous"
                return AddressMatchResult(
                    status=status,
                    query_variant=variant,
                    records=records,
                )
            return AddressMatchResult(status="no_match")
        except Exception as e:
            return AddressMatchResult(status="error", error=str(e))

    def _query_owner(self, owner_variant: str) -> list[ParcelRecord]:
        where = "OWNER_NAME LIKE '%{}%'".format(owner_variant.replace("'", "''"))
        params = {
            "f": "json",
            "where": where,
            "outFields": (
                "APN_DASH,OWNER_NAME,PHYSICAL_ADDRESS,MAIL_ADDRESS,"
                "PHYSICAL_CITY,PHYSICAL_ZIP,JURISDICTION"
            ),
            "returnGeometry": "false",
            "resultRecordCount": self.result_limit,
        }
        response = self.session.get(ASSESSOR_QUERY_URL, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise RuntimeError(data["error"])
        return [_parse_parcel(feature.get("attributes", {})) for feature in data.get("features", [])]


def _owner_search_variants(landlord_name: str) -> list[str]:
    variants: list[str] = []
    for part in re.split(r"[/;]", landlord_name):
        variant = _normalize_owner_name(part)
        if variant and variant not in variants:
            variants.append(variant)
    if not variants:
        normalized = _normalize_owner_name(landlord_name)
        if normalized:
            variants.append(normalized)
    return variants


def _normalize_owner_name(raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 &]+", " ", raw.upper())
    cleaned = cleaned.replace("&", " & ")
    return re.sub(r"\s+", " ", cleaned).strip()


def _parse_parcel(attrs: dict) -> ParcelRecord:
    return ParcelRecord(
        apn=str(attrs.get("APN_DASH") or "").strip(),
        owner_name=_clean_spaces(attrs.get("OWNER_NAME") or ""),
        physical_address=_clean_spaces(attrs.get("PHYSICAL_ADDRESS") or ""),
        mailing_address=_clean_spaces(attrs.get("MAIL_ADDRESS") or ""),
        physical_city=_clean_spaces(attrs.get("PHYSICAL_CITY") or ""),
        physical_zip=_clean_spaces(attrs.get("PHYSICAL_ZIP") or ""),
        jurisdiction=_clean_spaces(attrs.get("JURISDICTION") or ""),
    )


def _clean_spaces(raw: str) -> str:
    return re.sub(r"\s+", " ", str(raw)).strip()
