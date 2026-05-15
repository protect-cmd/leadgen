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


import re as _re

_APT_NO_RE = _re.compile(r'\bApartment\s+No\.?\s*', _re.IGNORECASE)


def _split_address(address: str) -> dict[str, str]:
    """
    Parse "123 Main St, Houston, TX 77001" back into BatchData address fields.
    Format produced by harris._build_address: street[, line2], city, STATE ZIP
    """
    address = _APT_NO_RE.sub("Apt ", address)
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


async def enrich_tenant_by_name(
    filing: Filing,
    lookup_property_if_missing: bool = True,
) -> EnrichedContact:
    """NG track for yellow calendar sources — no property address available.

    Chain: split_tenants → parse_name → cache → surname filter →
           ZIP resolve → daily cap → SearchBug → BatchData → cache store.
    """
    from dataclasses import replace as _dc_replace
    from services.name_utils import parse_name, split_tenants, is_common_surname, resolve_zip
    from services.searchbug_service import search_tenant as _searchbug_search
    from services.enrichment_cache import get_cache

    cache = get_cache()
    cap = int(os.environ.get("SEARCHBUG_DAILY_CAP", "100"))

    # Parse city/state from yellow-source address "City, STATE" or "City, STATE ZIP"
    raw_addr = (filing.property_address or "").strip()
    addr_parts = [p.strip() for p in raw_addr.split(",")]
    city = addr_parts[0] if addr_parts else ""
    state = filing.state
    postal_from_address = ""
    if len(addr_parts) >= 2:
        tokens = addr_parts[1].split()
        if tokens:
            state = tokens[0] or filing.state
        if len(tokens) >= 2 and tokens[1].isdigit():
            postal_from_address = tokens[1]

    for raw_name in split_tenants(filing.tenant_name.strip()):
        first_name, last_name = parse_name(raw_name)
        if not first_name or not last_name:
            log.info(f"enrich_tenant_by_name: unparseable name segment {raw_name!r} for {filing.case_number}")
            continue

        # Cache lookup — None = uncached; (None, None) = cached miss
        cached = cache.get(first_name, last_name, city, state)
        if cached is not None:
            phone, resolved_address = cached
            if resolved_address:
                patched = filing.model_copy(update={"property_address": resolved_address})
                result = await enrich_tenant(patched, lookup_property_if_missing=lookup_property_if_missing)
                if not result.phone and phone:
                    result = _dc_replace(result, phone=phone, dnc_source="searchbug")
                return result
            if phone:
                return EnrichedContact(
                    filing=filing, track="ng", phone=phone,
                    dnc_status="unknown", dnc_source="searchbug",
                )
            continue  # cached miss — try next name

        # Pre-call common-surname filter
        if is_common_surname(last_name):
            log.info(
                f"enrich_tenant_by_name: common surname skip {last_name!r} "
                f"for {filing.case_number}"
            )
            cache.set(first_name, last_name, city, state, None, None)
            continue

        # Daily cap check
        if not cache.check_daily_cap(cap):
            log.warning(f"enrich_tenant_by_name: daily cap {cap} reached for {filing.case_number}")
            break

        # ZIP narrowing — use address-derived ZIP first, then city map
        postal = postal_from_address or resolve_zip(city, state)

        phone, resolved_address = await _searchbug_search(
            first_name, last_name, city=city, state=state, postal=postal
        )
        cache.increment_daily_count()
        cache.set(first_name, last_name, city, state, phone, resolved_address)

        if resolved_address:
            patched = filing.model_copy(update={"property_address": resolved_address})
            result = await enrich_tenant(patched, lookup_property_if_missing=lookup_property_if_missing)
            if not result.phone and phone:
                result = _dc_replace(result, phone=phone, dnc_source="searchbug")
            return result

        if phone:
            log.info(f"enrich_tenant_by_name: SearchBug phone-only hit for {filing.case_number}")
            return EnrichedContact(
                filing=filing, track="ng", phone=phone,
                dnc_status="unknown", dnc_source="searchbug",
            )

    log.info(f"enrich_tenant_by_name: no match for {filing.case_number}")
    return EnrichedContact(filing=filing, track="ng", phone=None, email=None,
                           dnc_status="unknown", dnc_source=None)


async def enrich_tenant(
    filing: Filing,
    property_info: PropertyInfo | None = None,
    lookup_property_if_missing: bool = True,
    use_melissa_fallback: bool = True,
) -> EnrichedContact:
    """NG track — skip-traces the tenant by name at the property address.

    Falls back to Melissa Personator when BatchData returns no name match,
    provided MELISSA_LICENSE_KEY is set and use_melissa_fallback is True.
    """
    addr = _split_address(filing.property_address)
    headers = _headers()

    phone: str | None = None
    dnc_status = "unknown"
    dnc_source: str | None = None
    email: str | None = None
    property_type: str | None = filing.property_type_hint
    estimated_rent: float | None = filing.claim_amount
    name_matched = False

    # Normalize "LAST,FIRST" → "FIRST LAST" so name matching works
    raw_name = filing.tenant_name.strip()
    if "," in raw_name and raw_name.index(",") < len(raw_name) - 1:
        last, _, first = raw_name.partition(",")
        tenant_name_normalized = f"{first.strip()} {last.strip()}"
    else:
        tenant_name_normalized = raw_name

    async with httpx.AsyncClient(timeout=30) as client:
        # Skip-trace by property address — same endpoint as EC track
        # /api/v1/people/search does not exist; skip-trace returns the resident at the unit
        r = await client.post(
            f"{BASE}{SKIP_TRACE_EP}",
            json={"requests": [{"propertyAddress": addr}]},
            headers=headers,
        )
        if r.status_code == 200:
            persons = r.json().get("results", {}).get("persons", [])
            if persons:
                p = persons[0]
                # name field is a dict {first, last, middle, full}; fall back to fullName string
                name_field = p.get("name") or {}
                returned_name = (
                    name_field.get("full") if isinstance(name_field, dict) else name_field
                ) or p.get("fullName") or ""
                if _tenant_name_matches(tenant_name_normalized, returned_name):
                    name_matched = True
                    phone_selection = _best_phone_result(p.get("phoneNumbers", []))
                    phone = phone_selection.number
                    dnc_status = phone_selection.dnc_status
                    dnc_source = phone_selection.dnc_source
                    email = _best_email(p.get("emails", []))
                else:
                    log.info(
                        f"Tenant name mismatch for {filing.case_number}: "
                        f"expected={tenant_name_normalized!r}, got={returned_name!r} "
                        f"→ tenant_not_matched"
                    )
        else:
            log.warning(
                f"Tenant skip-trace {r.status_code} for {filing.case_number}: "
                f"{r.text[:200]}"
            )

    # Melissa fallback — runs when BatchData returned no name match
    if not phone and use_melissa_fallback and os.environ.get("MELISSA_LICENSE_KEY"):
        from services.melissa_service import lookup_tenant as _melissa_lookup
        name_parts = tenant_name_normalized.strip().split()
        first_name = name_parts[0] if name_parts else ""
        last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""
        if first_name and last_name:
            m_phone, m_email, m_matched = await _melissa_lookup(
                first_name, last_name, filing.property_address
            )
            if m_matched and m_phone:
                phone = m_phone
                email = email or m_email
                dnc_source = "melissa"
                log.info(f"Melissa fallback hit for {filing.case_number}: phone={phone}")

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
        f"name_matched={'yes' if name_matched else 'no'}"
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
