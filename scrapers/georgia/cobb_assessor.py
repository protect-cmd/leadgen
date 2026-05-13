from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

import requests

_QUERY_URL = (
    "https://gis.cobbcounty.gov/gisserver/rest/services/cobbpublic/Parcels/MapServer/0/query"
)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

AddressMatchStatus = Literal["single_match", "ambiguous", "no_match", "error"]


@dataclass(frozen=True)
class CobbParcelRecord:
    pin: str
    owner_nam1: str
    situs_addr: str
    owner_city: str
    owner_stat: str
    owner_zip: str


@dataclass(frozen=True)
class AddressMatchResult:
    status: AddressMatchStatus
    query_variant: str = ""
    records: list[CobbParcelRecord] = field(default_factory=list)
    error: str | None = None

    def __post_init__(self) -> None:
        if self.records is None:
            object.__setattr__(self, "records", [])


class CobbAssessorClient:
    """No-cost ArcGIS owner-name matcher for Cobb County GA parcel data."""

    def __init__(self, session: requests.Session | None = None, result_limit: int = 25):
        self.session = session or requests.Session()
        self.session.headers.update(_HEADERS)
        self.result_limit = result_limit

    def match_owner(self, landlord_name: str) -> AddressMatchResult:
        try:
            for variant in _owner_search_variants(landlord_name):
                records = self._query_owner(variant)
                if not records:
                    continue
                status: AddressMatchStatus = "single_match" if len(records) == 1 else "ambiguous"
                return AddressMatchResult(status=status, query_variant=variant, records=records)
            return AddressMatchResult(status="no_match")
        except Exception as e:
            return AddressMatchResult(status="error", error=str(e))

    def _query_owner(self, owner_variant: str) -> list[CobbParcelRecord]:
        where = "OWNER_NAM1 LIKE '%{}%'".format(owner_variant.replace("'", "''"))
        params = {
            "f": "json",
            "where": where,
            "outFields": "PIN,OWNER_NAM1,SITUS_ADDR,OWNER_CITY,OWNER_STAT,OWNER_ZIP",
            "returnGeometry": "false",
            "resultRecordCount": self.result_limit,
        }
        r = self.session.get(_QUERY_URL, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(str(data["error"]))
        return [_parse_record(f.get("attributes", {})) for f in data.get("features", [])]


def _owner_search_variants(name: str) -> list[str]:
    variants: list[str] = []
    for part in re.split(r"[/;]", name):
        v = _normalize_owner_name(part)
        if v and v not in variants:
            variants.append(v)
    if not variants:
        v = _normalize_owner_name(name)
        if v:
            variants.append(v)
    return variants


def _normalize_owner_name(raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 &]+", " ", raw.upper())
    return re.sub(r"\s+", " ", cleaned).strip()


def _parse_record(attrs: dict) -> CobbParcelRecord:
    def _clean(v: object) -> str:
        return re.sub(r"\s+", " ", str(v or "")).strip()

    return CobbParcelRecord(
        pin=_clean(attrs.get("PIN")),
        owner_nam1=_clean(attrs.get("OWNER_NAM1")),
        situs_addr=_clean(attrs.get("SITUS_ADDR")),
        owner_city=_clean(attrs.get("OWNER_CITY")),
        owner_stat=_clean(attrs.get("OWNER_STAT")),
        owner_zip=_clean(attrs.get("OWNER_ZIP")),
    )
