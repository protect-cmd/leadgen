from __future__ import annotations

import logging
import os

import httpx

from models.contact import EnrichedContact
from models.filing import Filing

log = logging.getLogger(__name__)

BASE = "https://api.batchdata.com"
SKIP_TRACE_EP = "/api/v1/property/skip-trace"
LOOKUP_EP = "/api/v1/property/lookup/all-attributes"


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


def _best_phone(phone_list: list[dict]) -> str | None:
    if not phone_list:
        return None
    # Prefer mobile, then highest score; exclude DNC numbers if alternatives exist
    non_dnc = [p for p in phone_list if not p.get("dnc")]
    pool = non_dnc if non_dnc else phone_list
    ranked = sorted(
        pool,
        key=lambda p: (p.get("type", "") == "Mobile", p.get("score", 0)),
        reverse=True,
    )
    return ranked[0].get("number")


def _best_email(email_list: list) -> str | None:
    if not email_list:
        return None
    first = email_list[0]
    return first.get("email") if isinstance(first, dict) else first


async def enrich(filing: Filing) -> EnrichedContact:
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

        # Property lookup: only needed when the scraper didn't supply type/rent
        if property_type is None or estimated_rent is None:
            r = await client.post(
                f"{BASE}{LOOKUP_EP}",
                json={"requests": [{"address": addr}]},
                headers=headers,
            )
            if r.status_code == 200:
                properties = r.json().get("results", {}).get("properties", [])
                if properties:
                    prop = properties[0]

                    if property_type is None:
                        cat = prop.get("propertyTypeCategory", "").lower()
                        if "commercial" in cat:
                            property_type = "commercial"
                        elif "residential" in cat:
                            property_type = "residential"

                    # Owner mailing address (where landlord receives mail)
                    mailing = prop.get("owner", {}).get("mailingAddress", {})
                    if mailing.get("street"):
                        secondary_address = (
                            f"{mailing['street']}, "
                            f"{mailing.get('city', '')}, "
                            f"{mailing.get('state', '')} {mailing.get('zip', '')}"
                        ).strip(", ")

                    # BatchData has no rent estimate; estimated_rent stays None
                    # for counties that don't provide claim_amount
            else:
                log.warning(f"Property lookup {r.status_code} for {filing.case_number}: {r.text[:200]}")

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


async def enrich_tenant(filing: Filing) -> EnrichedContact:
    """NG track — skip-traces the tenant by name at the property address."""
    addr = _split_address(filing.property_address)
    headers = _headers()

    phone: str | None = None
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
                phone = _best_phone(p.get("phoneNumbers", []))
                email = _best_email(p.get("emails", []))
        else:
            log.warning(
                f"Tenant skip-trace {r.status_code} for {filing.case_number}: "
                f"{r.text[:200]}"
            )

        # Reuse property lookup for type/rent — same property either way
        if property_type is None or estimated_rent is None:
            r = await client.post(
                f"{BASE}{LOOKUP_EP}",
                json={"requests": [{"address": addr}]},
                headers=headers,
            )
            if r.status_code == 200:
                properties = r.json().get("results", {}).get("properties", [])
                if properties:
                    prop = properties[0]
                    if property_type is None:
                        cat = prop.get("propertyTypeCategory", "").lower()
                        if "commercial" in cat:
                            property_type = "commercial"
                        elif "residential" in cat:
                            property_type = "residential"
            else:
                log.warning(
                    f"Property lookup {r.status_code} for {filing.case_number}: "
                    f"{r.text[:200]}"
                )

    log.info(
        f"BatchData (tenant) enriched {filing.case_number}: "
        f"phone={'yes' if phone else 'no'}, "
        f"email={'yes' if email else 'no'}, "
        f"property_type={property_type}"
    )

    return EnrichedContact(
        filing=filing,
        track="ng",
        phone=phone,
        email=email,
        estimated_rent=estimated_rent,
        property_type=property_type,
    )
