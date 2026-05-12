from __future__ import annotations

from dataclasses import dataclass
import logging
import os

import httpx

from models.contact import EnrichedContact

log = logging.getLogger(__name__)

BASE = "https://api.instantly.ai/api/v2"


@dataclass(frozen=True)
class InstantlyResult:
    enrolled: bool = False
    skipped_reason: str | None = None
    error: str | None = None


def is_enabled() -> bool:
    return os.getenv("INSTANTLY_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _campaign_id(track: str) -> str:
    var = "INSTANTLY_EC_CAMPAIGN_ID" if track == "ec" else "INSTANTLY_NG_CAMPAIGN_ID"
    return os.getenv(var, "")


def _headers() -> dict[str, str]:
    key = os.environ.get("INSTANTLY_API_KEY", "")
    if not key:
        raise RuntimeError("INSTANTLY_API_KEY not set")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _split_name(full_name: str) -> tuple[str, str]:
    parts = full_name.strip().split()
    if len(parts) >= 2:
        return parts[0].title(), " ".join(parts[1:]).title()
    return full_name.title(), ""


def _build_lead(contact: EnrichedContact) -> dict:
    first, last = _split_name(contact.contact_name)
    return {
        "email": contact.email,
        "first_name": first,
        "last_name": last,
        "custom_variables": {
            "county": contact.filing.county,
            "property_address": contact.filing.property_address,
            "case_number": contact.filing.case_number,
        },
    }


async def enroll(contact: EnrichedContact) -> InstantlyResult:
    """Enroll one contact into their track's Instantly campaign."""
    if not is_enabled():
        return InstantlyResult(skipped_reason="disabled")

    if not contact.email:
        return InstantlyResult(skipped_reason="missing_email")

    campaign_id = _campaign_id(contact.track)
    if not campaign_id:
        log.warning(
            "Instantly campaign ID not set for track %s - skipping %s",
            contact.track.upper(),
            contact.email,
        )
        return InstantlyResult(skipped_reason="missing_campaign_id")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{BASE}/leads/add",
                headers=_headers(),
                json={"campaign_id": campaign_id, "leads": [_build_lead(contact)]},
            )
            resp.raise_for_status()
            data = resp.json()
            uploaded = data.get("leads_uploaded", 0)
            dupes = data.get("duplicated_leads", 0)
            blocked = data.get("in_blocklist", 0)
            if blocked:
                log.info(
                    "Instantly blocklisted [%s]: %s",
                    contact.track.upper(),
                    contact.email,
                )
                return InstantlyResult(skipped_reason="blocklisted")
            if dupes and not uploaded:
                log.info(
                    "Instantly duplicate (already enrolled) [%s]: %s",
                    contact.track.upper(),
                    contact.email,
                )
                return InstantlyResult(skipped_reason="duplicate")
            log.info(
                "Instantly enrolled [%s]: %s",
                contact.track.upper(),
                contact.email,
            )
            return InstantlyResult(enrolled=True)
    except httpx.HTTPStatusError as e:
        msg = f"{contact.email} [{contact.track.upper()}]: HTTP {e.response.status_code}"
        log.warning("Instantly enroll failed - %s - %s", msg, e.response.text[:200])
        return InstantlyResult(error=msg)
    except Exception as e:
        msg = f"{contact.email} [{contact.track.upper()}]: {e}"
        log.warning("Instantly enroll error - %s", msg)
        return InstantlyResult(error=msg)


async def list_campaigns() -> list[dict]:
    """Utility: fetch all campaigns so you can get their IDs for Railway env vars."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{BASE}/campaigns", headers=_headers(), params={"limit": 100})
        resp.raise_for_status()
        return resp.json().get("items", [])
