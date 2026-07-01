from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

import httpx

from models.contact import EnrichedContact
from models.filing import Filing

log = logging.getLogger(__name__)
_STATE_ZIP_RE = re.compile(r"\b([A-Z]{2})\s+(\d{5})(?:-\d{4})?\b", re.IGNORECASE)

BASE = "https://api.batchdata.com"
SKIP_TRACE_EP = "/api/v1/property/skip-trace"
LOOKUP_EP = "/api/v1/property/lookup/all-attributes"


@dataclass(frozen=True)
class PhoneSelection:
    number: str | None


@dataclass(frozen=True)
class PropertyInfo:
    property_type: str | None = None
    secondary_address: str | None = None


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
    ranked = sorted(
        phone_list,
        key=lambda p: (p.get("type", "") == "Mobile", p.get("score", 0)),
        reverse=True,
    )
    return PhoneSelection(ranked[0].get("number"))


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
                phone = _best_phone(p.get("phoneNumbers", []))
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
    )


async def _maybe_alert_cap_hit(cap: int, source: str) -> None:
    """Fire a Pushover alert the first time the SearchBug daily cap is hit
    each day. Subsequent cap-hits stay silent so we don't spam during the
    rest of the day's runs.
    """
    from services.enrichment_cache import get_cache
    from services import notification_service
    cache = get_cache()
    if not cache.claim_alert_once_today("searchbug_daily_cap"):
        return
    await notification_service.send_alert(
        "SearchBug daily cap reached",
        (
            f"SearchBug daily cap of {cap} lookups has been reached "
            f"(triggered by {source}). Remaining tenant leads today will "
            "skip SearchBug enrichment. Bump SEARCHBUG_DAILY_CAP on Railway "
            "if you want today's afternoon-county runs to keep enriching."
        ),
        priority=0,
        tags={"source": source, "cap": str(cap)},
    )


async def _searchbug_fallback_gated(
    filing: Filing,
    tenant_name_normalized: str,
):
    """Green-source SearchBug fallback for a single tenant name with cost gates.

    Returns a SearchBugResult (with .status, .phone, .resolved_address) when a
    call was made or a cached entry was found. Returns None when no call could
    be made (unparseable name, common surname, breaker tripped, cap hit) —
    indistinguishable from "no signal" downstream.
    """
    from services.enrichment_cache import get_cache
    from services.name_utils import is_common_surname, parse_name
    from services.searchbug_service import (
        query_full_street_address,
        search_tenant_detailed,
        SearchBugResult,
    )

    first_name, last_name = parse_name(tenant_name_normalized)
    if not first_name or not last_name:
        return None

    # Extract city/state/postal from the tail of addresses like:
    # "Street, City, ST ZIP" or "Street, UNIT 2, City, ST ZIP".
    addr_parts = [p.strip() for p in filing.property_address.split(",")]
    sb_city = addr_parts[-2] if len(addr_parts) >= 2 else ""
    sb_state = filing.state
    sb_postal = ""
    if addr_parts:
        match = _STATE_ZIP_RE.search(addr_parts[-1])
        if match:
            sb_state = match.group(1).upper()
            sb_postal = match.group(2)
    query_address = query_full_street_address(filing.property_address)

    cache = get_cache()
    cap = int(os.environ.get("SEARCHBUG_DAILY_CAP", "100"))

    cached = cache.get(
        first_name, last_name, sb_city, sb_state,
        postal=sb_postal,
        query_address=query_address,
    )
    if cached is not None:
        phone, addr = cached
        if phone:
            log.info(
                f"SearchBug cache hit for {filing.case_number}: {first_name} {last_name}"
            )
            return SearchBugResult(
                "phone_found", phone=phone, resolved_address=addr, rows=1,
            )
        # Cached miss — treat as no_records so the caller can distinguish from
        # "never called" via the None return below.
        return SearchBugResult("no_records")

    # Common-surname filter only applies when we lack BOTH a narrowing street and
    # a postal code. With a clean street+ZIP, SearchBug can disambiguate even
    # "Smith" to a single match. Without either, "Smith" in a whole city returns
    # an ambiguous batch we'd pay for and reject — skip the call.
    if is_common_surname(last_name) and not (query_address and sb_postal):
        log.info(
            f"SearchBug skipped (common surname {last_name!r}, no narrowing address) "
            f"for {filing.case_number}"
        )
        cache.set(
            first_name, last_name, sb_city, sb_state, None, None,
            postal=sb_postal,
            query_address=query_address,
        )
        return None

    # Circuit breaker — if SearchBug account is in error state, skip without
    # burning the daily cap counter (and without making a doomed HTTP call).
    from services.searchbug_service import is_account_error_tripped
    if is_account_error_tripped():
        log.info(
            f"SearchBug circuit breaker tripped, skipping {filing.case_number}"
        )
        return None

    if not cache.check_daily_cap(cap):
        log.warning(
            f"SearchBug daily cap {cap} reached — skipping {filing.case_number}"
        )
        await _maybe_alert_cap_hit(cap, source=f"green/{filing.state}/{filing.county}")
        return None

    result = await search_tenant_detailed(
        first_name, last_name, sb_city, sb_state, sb_postal,
        address=query_address,
        strip_unit=False,
    )
    # Only count against the daily cap if we actually hit the wire. If the
    # call just tripped the breaker, we don't want to consume a slot.
    if not is_account_error_tripped():
        cache.increment_daily_count()
    cache.set(
        first_name, last_name, sb_city, sb_state, result.phone, result.resolved_address,
        postal=sb_postal,
        query_address=query_address,
    )

    if result.phone:
        log.info(
            f"SearchBug {result.status} for {filing.case_number}: phone={result.phone}"
        )
    else:
        log.info(f"SearchBug {result.status} for {filing.case_number}: no phone")
    return result


async def enrich_tenant(
    filing: Filing,
    property_info: PropertyInfo | None = None,
    lookup_property_if_missing: bool = True,
) -> EnrichedContact:
    """NG track — find the tenant's phone via SearchBug people-search.

    BatchData's property skip-trace returns the property *owner*, not the
    resident, so historically only ~9% of tenant queries matched the filing
    name. Calling BatchData first burned a call to confirm "not the tenant"
    before falling through to SearchBug anyway. We now go straight to
    SearchBug (gated by cache, common-surname filter, daily cap) and rely
    on the shared property_info lookup for commercial/residential routing.
    """
    phone: str | None = None
    email: str | None = None
    property_type: str | None = filing.property_type_hint
    estimated_rent: float | None = filing.claim_amount
    searchbug_status: str | None = None
    searchbug_returned_name: str | None = None

    # Normalize "LAST,FIRST" → "FIRST LAST" so name parsing works downstream
    raw_name = filing.tenant_name.strip()
    if "," in raw_name and raw_name.index(",") < len(raw_name) - 1:
        last, _, first = raw_name.partition(",")
        tenant_name_normalized = f"{first.strip()} {last.strip()}"
    else:
        tenant_name_normalized = raw_name

    result = await _searchbug_fallback_gated(filing, tenant_name_normalized)
    if result is not None:
        searchbug_status = result.status
        if result.phone:
            phone = result.phone

    if property_info is None and lookup_property_if_missing and property_type is None:
        property_info = await lookup_property_info(filing)
    property_type, _ = _apply_property_info(
        property_type,
        None,
        property_info,
    )

    log.info(
        f"SearchBug (tenant) enriched {filing.case_number}: "
        f"phone={'yes' if phone else 'no'}, "
        f"status={searchbug_status}, "
        f"property_type={property_type}"
    )

    return EnrichedContact(
        filing=filing,
        track="ng",
        phone=phone,
        email=email,
        estimated_rent=estimated_rent,
        property_type=property_type,
        searchbug_status=searchbug_status,
        searchbug_returned_name=searchbug_returned_name,
    )
