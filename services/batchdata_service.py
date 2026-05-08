from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

from models.contact import EnrichedContact
from models.filing import Filing

log = logging.getLogger(__name__)

BASE = "https://api.batchdata.com"
SKIP_TRACE_EP = "/api/v1/property/skip-trace"
LOOKUP_EP = "/api/v1/property/lookup/all-attributes"


@dataclass(frozen=True)
class PhoneSelection:
    number: str | None
    dnc_status: str = "unknown"
    dnc_source: str | None = None


@dataclass(frozen=True)
class PropertyInfo:
    property_type: str | None = None
    secondary_address: str | None = None


def _dnc_status(phone: dict) -> str:
    if "dnc" not in phone or phone.get("dnc") is None:
        return "unknown"
    return "blocked" if phone.get("dnc") else "clear"


def _headers() -> dict[str, str]:
    key = os.environ.get("BATCHDATA_API_KEY", "")
    if not key:
        raise RuntimeError("BATCHDATA_API_KEY not set")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _split_address(address: str) -> dict[str, str]:
    """
    Parse "123 Main St, Houston, TX 77001" back into BatchData address fields.
    Format produced by harris._build_address: street[, line2], city, STATE ZIP
    """
    parts = [p.strip() for p in address.split(",")]
    state = zip_ = city = ""
    street_parts: list[str] = []

    if len(parts) >= 3:
        tokens = parts[-1].split()
        if len(tokens) >= 2:
            state, zip_ = tokens[0], tokens[1]
        elif tokens:
            state = tokens[0]
        city = parts[-2]
        street_parts = parts[:-2]
    elif len(parts) == 2:
        city = parts[-1]
        street_parts = parts[:1]
    else:
        street_parts = parts

    return {
        "street": ", ".join(street_parts),
        "city": city,
        "state": state,
        "zip": zip_,
    }


_COMPANY_TERMS = {
    "llc", "inc", "corp", "lp", "llp", "trust", "properties", "apartments",
    "management", "holdings", "group", "realty", "enterprises",
}

_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}

_STRIP_CHARS = str.maketrans("", "", ".,'-")


def _tenant_name_matches(expected: str, returned: str | None) -> bool:
    """Return True if *returned* is a plausible person name that matches *expected*.

    Rules:
    - Rejects None / empty strings.
    - Rejects names containing company/legal-entity terms (LLC, Inc, …).
    - Case-insensitive; strips punctuation (.,'-).
    - Ignores generational suffixes (Jr, Sr, II, III, IV).
    - First + last name match is sufficient; middle name is not required.
    """
    if not returned or not returned.strip():
        return False

    def _normalise(name: str) -> list[str]:
        tokens = name.strip().lower().translate(_STRIP_CHARS).split()
        return [t for t in tokens if t not in _NAME_SUFFIXES]

    ret_tokens = _normalise(returned)

    # Reject if any company/legal-entity term appears in the returned name.
    if any(t in _COMPANY_TERMS for t in ret_tokens):
        return False

    exp_tokens = _normalise(expected)
    if not exp_tokens or not ret_tokens:
        return False

    # Full token-set match (order-independent).
    if set(exp_tokens) == set(ret_tokens):
        return True

    # First + last from expected are both present in returned.
    first = exp_tokens[0]
    last = exp_tokens[-1] if len(exp_tokens) > 1 else None
    if last and first in ret_tokens and last in ret_tokens:
        return True

    return False


def _best_phone_result(phone_list: list[dict]) -> PhoneSelection:
    if not phone_list:
        return PhoneSelection(None)
    # Prefer confirmed-clear numbers, then unknowns, and only use DNC hits if no alternative exists.
    pool = [p for p in phone_list if _dnc_status(p) == "clear"]
    if not pool:
        pool = [p for p in phone_list if _dnc_status(p) == "unknown"]
    if not pool:
        pool = phone_list
    ranked = sorted(
        pool,
        key=lambda p: (p.get("type", "") == "Mobile", p.get("score", 0)),
        reverse=True,
    )
    selected = ranked[0]
    return PhoneSelection(
        selected.get("number"),
        _dnc_status(selected),
        "batchdata",
    )


def _best_phone(phone_list: list[dict]) -> str | None:
    return _best_phone_result(phone_list).number


def _best_email(email_list: list) -> str | None:
    if not email_list:
        return None
    first = email_list[0]
    return first.get("email") if isinstance(first, dict) else first


def _property_info_from_property(prop: dict) -> PropertyInfo:
    property_type = None
    cat = prop.get("propertyTypeCategory", "").lower()
    if "commercial" in cat:
        property_type = "commercial"
    elif "residential" in cat:
        property_type = "residential"

    secondary_address = None
    mailing = prop.get("owner", {}).get("mailingAddress", {})
    if mailing.get("street"):
        secondary_address = (
            f"{mailing['street']}, "
            f"{mailing.get('city', '')}, "
            f"{mailing.get('state', '')} {mailing.get('zip', '')}"
        ).strip(", ")

    return PropertyInfo(property_type=property_type, secondary_address=secondary_address)


async def lookup_property_info(filing: Filing) -> PropertyInfo:
    addr = _split_address(filing.property_address)
    headers = _headers()

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{BASE}{LOOKUP_EP}",
            json={"requests": [{"address": addr}]},
            headers=headers,
        )
    if r.status_code != 200:
        log.warning(f"Property lookup {r.status_code} for {filing.case_number}: {r.text[:200]}")
        return PropertyInfo()

    properties = r.json().get("results", {}).get("properties", [])
    if not properties:
        return PropertyInfo()
    return _property_info_from_property(properties[0])


def _apply_property_info(
    property_type: str | None,
    secondary_address: str | None,
    property_info: PropertyInfo | None,
) -> tuple[str | None, str | None]:
    if property_info is None:
        return property_type, secondary_address
    return (
        property_type or property_info.property_type,
        secondary_address or property_info.secondary_address,
    )


async def enrich(
    filing: Filing,
    property_info: PropertyInfo | None = None,
    lookup_property_if_missing: bool = True,
) -> EnrichedContact:
    """EC track — skip-traces the property to find the landlord/owner."""
    addr = _split_address(filing.property_address)
    headers = _headers()

    phone: str | None = None
    dnc_status = "unknown"
    dnc_source: str | None = None
    email: str | None = None
    secondary_address: str | None = None
    property_type: str | None = filing.property_type_hint
    estimated_rent: float | None = filing.claim_amount

    async with httpx.AsyncClient(timeout=30) as client:
        # Skip-trace: phone and email for the property address
        r = await client.post(
            f"{BASE}{SKIP_TRACE_EP}",
            json={"requests": [{"propertyAddress": addr}]},
            headers=headers,
        )
        if r.status_code == 200:
            persons = r.json().get("results", {}).get("persons", [])
            if persons:
                p = persons[0]
                phone_selection = _best_phone_result(p.get("phoneNumbers", []))
                phone = phone_selection.number
                dnc_status = phone_selection.dnc_status
                dnc_source = phone_selection.dnc_source
                email = _best_email(p.get("emails", []))
        else:
            log.warning(f"Skip-trace {r.status_code} for {filing.case_number}: {r.text[:200]}")

    if property_info is None and lookup_property_if_missing and property_type is None:
        property_info = await lookup_property_info(filing)
    property_type, secondary_address = _apply_property_info(
        property_type,
        secondary_address,
        property_info,
    )

    log.info(
        f"BatchData enriched {filing.case_number}: "
        f"phone={'yes' if phone else 'no'}, "
        f"email={'yes' if email else 'no'}, "
        f"property_type={property_type}, "
        f"estimated_rent={estimated_rent}"
    )

    return EnrichedContact(
        filing=filing,
        track="ec",
        phone=phone,
        email=email,
        secondary_address=secondary_address,
        estimated_rent=estimated_rent,
        property_type=property_type,
        dnc_status=dnc_status,
        dnc_source=dnc_source,
    )


async def enrich_tenant(
    filing: Filing,
    property_info: PropertyInfo | None = None,
    lookup_property_if_missing: bool = True,
) -> EnrichedContact:
    """NG track — skip-traces the tenant by name at the property address."""
    addr = _split_address(filing.property_address)
    headers = _headers()

    phone: str | None = None
    dnc_status = "unknown"
    dnc_source: str | None = None
    email: str | None = None
    property_type: str | None = filing.property_type_hint
    estimated_rent: float | None = filing.claim_amount

    name_parts = filing.tenant_name.strip().split()
    first_name = name_parts[0] if name_parts else ""
    last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

    async with httpx.AsyncClient(timeout=30) as client:
        # People search by name + address — returns the tenant's contact info
        r = await client.post(
            f"{BASE}/api/v1/people/search",
            json={
                "requests": [{
                    "firstName": first_name,
                    "lastName": last_name,
                    "address": addr,
                }]
            },
            headers=headers,
        )
        if r.status_code == 200:
            persons = r.json().get("results", {}).get("persons", [])
            if persons:
                p = persons[0]
                returned_name = p.get("fullName") or p.get("name") or ""
                if _tenant_name_matches(filing.tenant_name, returned_name):
                    phone_selection = _best_phone_result(p.get("phoneNumbers", []))
                    phone = phone_selection.number
                    dnc_status = phone_selection.dnc_status
                    dnc_source = phone_selection.dnc_source
                    email = _best_email(p.get("emails", []))
                else:
                    log.info(
                        f"Tenant name mismatch for {filing.case_number}: "
                        f"expected={filing.tenant_name!r}, got={returned_name!r} "
                        f"→ tenant_not_matched"
                    )
        else:
            log.warning(
                f"Tenant skip-trace {r.status_code} for {filing.case_number}: "
                f"{r.text[:200]}"
            )

    if property_info is None and lookup_property_if_missing and property_type is None:
        property_info = await lookup_property_info(filing)
    property_type, _ = _apply_property_info(
        property_type,
        None,
        property_info,
    )

    log.info(
        f"BatchData (tenant) enriched {filing.case_number}: "
        f"phone={'yes' if phone else 'no'}, "
        f"email={'yes' if email else 'no'}, "
        f"property_type={property_type}, "
        f"name_matched={'yes' if phone else 'no'}"
    )

    return EnrichedContact(
        filing=filing,
        track="ng",
        phone=phone,
        email=email,
        estimated_rent=estimated_rent,
        property_type=property_type,
        dnc_status=dnc_status,
        dnc_source=dnc_source,
    )
