"""EnformionGO (Endato) Contact-Enrich enrichment for the NG (tenant) track.

Drop-in alternative to SearchBug for finding a tenant's phone by name+address.
Bills for SUCCESSFUL MATCHES ONLY (no-match calls are free) and has no daily-cap
/ credit-depletion behaviour, so it sidesteps the SearchBug top-up wall.

Returns an EnrichedContact shaped identically to batchdata_service.enrich_tenant
so the rest of the pipeline (routing, GHL, Bland) is unchanged. The `searchbug_status`
field is reused to carry the match verdict:
    phone_found | name_mismatch | no_phone | no_records | enformion_error
"""
from __future__ import annotations

import logging
import os

import httpx

from models.contact import EnrichedContact
from models.filing import Filing
from services.batchdata_service import _split_address, _tenant_name_matches
from services.name_utils import parse_name

log = logging.getLogger(__name__)

ENDPOINT = "https://devapi.enformion.com/Contact/Enrich"
SEARCH_TYPE = "DevAPIContactEnrich"


def _headers() -> dict[str, str]:
    name = os.environ.get("ENFORMION_AP_NAME", "")
    pw = os.environ.get("ENFORMION_AP_PASSWORD", "")
    if not name or not pw:
        raise RuntimeError("ENFORMION_AP_NAME / ENFORMION_AP_PASSWORD not set")
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "galaxy-ap-name": name,
        "galaxy-ap-password": pw,
        "galaxy-search-type": SEARCH_TYPE,
    }


def _norm_phone(number: str | None) -> str | None:
    """Bare 10-digit form to match SearchBug-stored phones (Bland/GHL/DNC expect this)."""
    d = "".join(ch for ch in (number or "") if ch.isdigit())
    if len(d) == 11 and d[0] == "1":
        d = d[1:]
    return d if len(d) == 10 else None


def _best_phone(phones: list[dict]) -> str | None:
    """Pick the strongest phone: connected first, then Mobile/Wireless, then recency."""
    if not phones:
        return None
    def rank(p: dict):
        t = (p.get("type") or "").lower()
        return (
            bool(p.get("isConnected")),
            "mobile" in t or "wireless" in t,
            p.get("lastReportedDate") or "",
        )
    for p in sorted(phones, key=rank, reverse=True):
        n = _norm_phone(p.get("number"))
        if n:
            return n
    return None


def _returned_name(person: dict) -> str:
    n = person.get("name") or {}
    return " ".join(p for p in [n.get("firstName"), n.get("middleName"), n.get("lastName")] if p).strip()


async def enrich_tenant(
    filing: Filing,
    property_info=None,
    lookup_property_if_missing: bool = False,
) -> EnrichedContact:
    """NG track — find the tenant's phone via EnformionGO Contact Enrich."""
    property_type = filing.property_type_hint
    estimated_rent = filing.claim_amount

    # Normalize "LAST,FIRST" -> first/last
    first, last = parse_name(filing.tenant_name)
    if not first or not last:
        return EnrichedContact(filing=filing, track="ng", property_type=property_type,
                               estimated_rent=estimated_rent, searchbug_status="unparseable_name")

    addr = _split_address(filing.property_address)
    line2 = ", ".join(p for p in [addr.get("city"), addr.get("state")] if p)
    payload: dict = {
        "FirstName": first,
        "LastName": last,
        "Address": {"addressLine1": addr.get("street", ""), "addressLine2": line2},
    }

    status = "no_records"
    phone = email = None
    ret_name = None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(ENDPOINT, headers=_headers(), json=payload)
        if r.status_code != 200:
            log.warning(f"Enformion {r.status_code} for {filing.case_number}: {r.text[:200]}")
            status = "enformion_error"
        else:
            data = r.json()
            person = data.get("person") or {}
            if person:
                ret_name = _returned_name(person)
                phone = _best_phone(person.get("phones") or [])
                emails = person.get("emails") or []
                if emails:
                    e0 = emails[0]
                    email = e0.get("email") if isinstance(e0, dict) else e0
                if phone and _tenant_name_matches(f"{first} {last}", ret_name):
                    status = "phone_found"
                elif phone:
                    status = "name_mismatch"
                else:
                    status = "no_phone"
    except httpx.HTTPError as e:
        log.warning(f"Enformion HTTP error for {filing.case_number}: {e!r}")
        status = "enformion_error"

    # name_mismatch -> don't trust the phone for auto-dial; drop it to no-phone lane
    use_phone = phone if status == "phone_found" else None

    log.info(f"Enformion (tenant) {filing.case_number}: status={status} "
             f"phone={'yes' if use_phone else 'no'} returned={ret_name!r}")

    return EnrichedContact(
        filing=filing, track="ng", phone=use_phone, email=email,
        estimated_rent=estimated_rent, property_type=property_type,
        searchbug_status=status, searchbug_returned_name=ret_name,
    )
