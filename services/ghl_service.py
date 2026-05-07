from __future__ import annotations

import logging
import os

import httpx

from models.contact import EnrichedContact
from services import notification_service

log = logging.getLogger(__name__)

BASE = "https://services.leadconnectorhq.com"
API_VERSION = "2021-07-28"

# module-level cache: stage_id -> pipeline_id (populated on first run)
_pipeline_cache: dict[str, str] = {}


def _is_duplicate_opportunity_error(status_code: int, body: str) -> bool:
    return (
        status_code == 400
        and "duplicate opportunity" in body.lower()
        and "contact" in body.lower()
    )


def _headers(track: str = "ec") -> dict[str, str]:
    if track == "ng":
        key = os.environ.get("GHL_API_NG_KEY") or os.environ.get("GHL_API_KEY", "")
    else:
        key = os.environ.get("GHL_API_KEY", "")
    if not key:
        raise RuntimeError("GHL_API_KEY not set")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Version": API_VERSION,
    }


def _location_id(track: str = "ec") -> str:
    var = "GHL_EC_LOCATION_ID" if track == "ec" else "GHL_NG_LOCATION_ID"
    lid = os.environ.get(var, "")
    if not lid:
        raise RuntimeError(f"{var} not set")
    return lid


def _split_name(full_name: str) -> tuple[str, str]:
    parts = full_name.strip().split()
    if len(parts) >= 2:
        return parts[0].title(), " ".join(parts[1:]).title()
    return full_name.title(), ""


async def _get_pipeline_id(
    client: httpx.AsyncClient,
    headers: dict,
    location_id: str,
    stage_id: str,
) -> str | None:
    if stage_id in _pipeline_cache:
        return _pipeline_cache[stage_id]

    r = await client.get(
        f"{BASE}/opportunities/pipelines",
        params={"locationId": location_id},
        headers=headers,
    )
    if r.status_code != 200:
        log.warning(f"Failed to fetch pipelines: {r.status_code} {r.text[:200]}")
        return None

    for pipeline in r.json().get("pipelines", []):
        for stage in pipeline.get("stages", []):
            _pipeline_cache[stage["id"]] = pipeline["id"]

    return _pipeline_cache.get(stage_id)


async def create_contact(
    contact: EnrichedContact,
    tags: list[str],
    pipeline_stage_id: str,
) -> str:
    if not (contact.phone or contact.email):
        raise RuntimeError("GHL contact requires phone or email from enrichment")

    location_id = _location_id(contact.track)
    headers = _headers(contact.track)
    filing = contact.filing

    first, last = _split_name(contact.contact_name)

    # Custom fields confirmed in GHL (verified via API 2026-05-01).
    # contact.filing_county and contact.case_number were created by us.
    # contact.property_type is SINGLE_OPTIONS — only set for "commercial" since
    # BatchData doesn't give us specific residential sub-type.
    # contact.monthly_rent_amount is the existing rent field (NUMERICAL).
    custom_fields: list[dict] = [
        {"key": "contact.filing_county", "field_value": filing.county},
        {"key": "contact.case_number", "field_value": filing.case_number},
    ]
    if contact.property_type == "commercial":
        custom_fields.append({"key": "contact.property_type", "field_value": "Commercial"})
    if contact.estimated_rent:
        custom_fields.append(
            {"key": "contact.monthly_rent_amount", "field_value": contact.estimated_rent}
        )

    upsert_payload: dict = {
        "locationId": location_id,
        "firstName": first,
        "lastName": last,
        "tags": tags,
        "source": "Eviction Filing",
        "address1": filing.property_address,
        "customFields": custom_fields,
    }
    if contact.phone:
        upsert_payload["phone"] = contact.phone
    if contact.email:
        upsert_payload["email"] = contact.email

    async with httpx.AsyncClient(timeout=30) as client:
        # Use upsert when we have a phone or email (GHL deduplicates on those).
        # Fall back to plain create when neither is available.
        if contact.phone or contact.email:
            r = await client.post(
                f"{BASE}/contacts/upsert",
                json=upsert_payload,
                headers=headers,
            )
        else:
            r = await client.post(
                f"{BASE}/contacts/",
                json=upsert_payload,
                headers=headers,
            )

        if r.status_code not in (200, 201):
            raise RuntimeError(
                f"GHL contact create failed {r.status_code}: {r.text[:300]}"
            )

        contact_data = r.json().get("contact", {})
        contact_id: str = contact_data["id"]
        log.info(f"GHL contact created/upserted: {contact_id}")

        # Add a note with the filing details so agents have full context
        note_body = (
            f"Case: {filing.case_number}\n"
            f"Filing Date: {filing.filing_date}\n"
            f"Court Date: {filing.court_date or 'N/A'}\n"
            f"Property: {filing.property_address}\n"
            f"Landlord: {filing.landlord_name}\n"
            f"Notice Type: {filing.notice_type}\n"
            f"Property Type: {contact.property_type or 'Unknown'}\n"
            f"Estimated Rent: {'${:,.0f}'.format(contact.estimated_rent) if contact.estimated_rent else 'N/A'}\n"
            f"Source: {filing.source_url}"
        )
        note_r = await client.post(
            f"{BASE}/contacts/{contact_id}/notes",
            json={"body": note_body, "userId": ""},
            headers=headers,
        )
        if note_r.status_code not in (200, 201):
            log.warning(f"GHL note creation failed: {note_r.status_code}")

        # Create opportunity in the correct pipeline stage (if stage ID is set)
        if pipeline_stage_id:
            pipeline_id = await _get_pipeline_id(
                client, headers, location_id, pipeline_stage_id
            )
            if pipeline_id:
                opp_payload: dict = {
                    "locationId": location_id,
                    "contactId": contact_id,
                    "name": f"{filing.case_number} — {filing.tenant_name.title()}",
                    "pipelineId": pipeline_id,
                    "pipelineStageId": pipeline_stage_id,
                    "status": "open",
                }
                if contact.estimated_rent:
                    opp_payload["monetaryValue"] = contact.estimated_rent

                opp_r = await client.post(
                    f"{BASE}/opportunities/",
                    json=opp_payload,
                    headers=headers,
                )
                if opp_r.status_code in (200, 201):
                    opp_id = opp_r.json().get("opportunity", {}).get("id", "")
                    log.info(f"GHL opportunity created: {opp_id}")
                elif _is_duplicate_opportunity_error(opp_r.status_code, opp_r.text):
                    log.info(
                        "GHL opportunity already exists for contact %s; "
                        "treating as idempotent",
                        contact_id,
                    )
                else:
                    error_msg = (
                        f"GHL opportunity creation failed {opp_r.status_code}: "
                        f"{opp_r.text[:200]}"
                    )
                    log.warning(
                        error_msg
                    )
                    await notification_service.send_job_error(
                        job=f"{filing.state}/{filing.county}",
                        stage=f"ghl_opportunity_{contact.track}",
                        error=error_msg,
                    )
            else:
                log.warning(
                    f"Could not resolve pipeline for stage {pipeline_stage_id} — "
                    "opportunity not created, contact tagged only"
                )

    return contact_id
