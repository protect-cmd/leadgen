"""ISTS Sub-Project B — GHL contact push for ists_judgments.

Reads enriched records (phone IS NOT NULL, ghl_contact_id IS NULL),
upserts contacts into the ISTS GHL subaccount, creates opportunity in
'New Filing' stage, writes ghl_contact_id + ghl_pushed_at back.
Writes only ists_judgments. Never touches filings/lead_contacts.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

log = logging.getLogger(__name__)

_TABLE = "ists_judgments"
_BASE = "https://services.leadconnectorhq.com"
_API_VERSION = "2021-07-28"

# ISTS GHL subaccount — confirmed 2026-06-08
_LOCATION_ID = os.environ.get("GHL_ISTS_LOCATION_ID", "")
_API_KEY = os.environ.get("GHL_API_ISTS_KEY", "")
_STAGE_ID = os.environ.get("GHL_ISTS_NEW_FILING_STAGE_ID", "")

# Custom field UUIDs in the ISTS subaccount (verified via API 2026-06-08)
_FIELD_IDS = {
    "language_preference": "0iHEXmTTmFr8Y3iY7DYy",
    "situation":           "S1MyRKxpOG5zm9wfJORz",
    "court_date":          "rJsXFkeaGWNW6xnvgcvO",
    "state":               "vYUeApmue3hYduk5il2p",
    "county":              "wUqEZ4qF87iDUaG3HU3g",
}

_client: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)

_pipeline_id_cache: str | None = None


def _headers() -> dict:
    if not _API_KEY:
        raise RuntimeError("GHL_API_ISTS_KEY not set")
    return {
        "Authorization": f"Bearer {_API_KEY}",
        "Content-Type": "application/json",
        "Version": _API_VERSION,
    }


def _split_name(full_name: str) -> tuple[str, str]:
    import re
    name = re.sub(r"\s+(?:and\s+)?all\s+(?:other\s+)?occupants?.*$", "", full_name,
                  flags=re.IGNORECASE).strip()
    if "," in name:
        last, _, first = name.partition(",")
        return first.strip().split()[0].title(), last.strip().title()
    parts = name.split()
    return (parts[0].title(), " ".join(parts[1:]).title()) if len(parts) >= 2 else (name.title(), "")


async def _get_pipeline_id(client: httpx.AsyncClient) -> str | None:
    global _pipeline_id_cache
    if _pipeline_id_cache:
        return _pipeline_id_cache
    r = await client.get(
        f"{_BASE}/opportunities/pipelines",
        params={"locationId": _LOCATION_ID},
        headers=_headers(),
    )
    if r.status_code != 200:
        log.warning("ISTS GHL: failed to fetch pipelines %s", r.status_code)
        return None
    for pipeline in r.json().get("pipelines", []):
        for stage in pipeline.get("stages", []):
            if stage["id"] == _STAGE_ID:
                _pipeline_id_cache = pipeline["id"]
                return _pipeline_id_cache
    return None


async def push_contact(rec: dict, dry_run: bool = False) -> str | None:
    """Upsert one ists_judgments record to GHL ISTS subaccount.
    Returns GHL contact ID or None on failure.
    """
    if not _LOCATION_ID or not _API_KEY:
        raise RuntimeError("GHL_ISTS_LOCATION_ID or GHL_API_ISTS_KEY not set")

    first, last = _split_name(rec["defendant_name"])
    lang = rec.get("language_hint") or "english_likely"
    lang_value = "Spanish" if lang == "spanish_likely" else "English"

    custom_fields = [
        {"id": _FIELD_IDS["language_preference"], "field_value": lang_value},
        {"id": _FIELD_IDS["situation"], "field_value": "Judgment entered — Window 1"},
        {"id": _FIELD_IDS["state"], "field_value": rec.get("state") or "TX"},
        {"id": _FIELD_IDS["county"], "field_value": rec.get("county") or "Harris"},
    ]
    if rec.get("judgment_date"):
        custom_fields.append({"id": _FIELD_IDS["court_date"],
                               "field_value": str(rec["judgment_date"])})

    payload = {
        "locationId": _LOCATION_ID,
        "firstName": first,
        "lastName": last,
        "phone": rec["phone"],
        "address1": rec.get("property_address", ""),
        "tags": ["ISTS", "W1", "ists_new_lead"],
        "source": "ISTS Harris Judgment",
        "customFields": custom_fields,
    }

    if dry_run:
        log.info("DRY GHL %s | %s %s | %s | lang=%s",
                 rec["case_number"], first, last, rec["phone"][:4] + "****", lang_value)
        return "dry-run"

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{_BASE}/contacts/upsert",
            json=payload,
            headers=_headers(),
        )
        if r.status_code not in (200, 201):
            log.error("ISTS GHL upsert failed %s: %s", r.status_code, r.text[:300])
            return None

        contact_id: str = r.json().get("contact", {}).get("id", "")
        log.info("ISTS GHL contact upserted: %s → %s", rec["case_number"], contact_id)

        # Note with judgment details
        note = (
            f"ISTS Window 1 — Judgment Entered\n"
            f"Case: {rec['case_number']}\n"
            f"Judgment Date: {rec.get('judgment_date') or 'N/A'}\n"
            f"Judgment Against: {rec.get('judgment_against') or 'N/A'}\n"
            f"Plaintiff: {rec.get('plaintiff_name') or 'N/A'}\n"
            f"Address: {rec.get('property_address') or 'N/A'}\n"
            f"Source: Harris JP Civil Extract"
        )
        await client.post(
            f"{_BASE}/contacts/{contact_id}/notes",
            json={"body": note, "userId": ""},
            headers=_headers(),
        )

        # Opportunity in New Filing stage
        pipeline_id = await _get_pipeline_id(client)
        if pipeline_id and _STAGE_ID:
            opp_r = await client.post(
                f"{_BASE}/opportunities/",
                json={
                    "locationId": _LOCATION_ID,
                    "contactId": contact_id,
                    "name": f"{rec['case_number']} — {first} {last}",
                    "pipelineId": pipeline_id,
                    "pipelineStageId": _STAGE_ID,
                    "status": "open",
                },
                headers=_headers(),
            )
            if opp_r.status_code in (200, 201):
                log.info("ISTS GHL opportunity created for %s", contact_id)

    return contact_id


async def push_batch(limit: int = 50, dry_run: bool = False) -> dict:
    """Push up to `limit` enriched, unpushed records to GHL ISTS subaccount."""
    def _fetch() -> list[dict]:
        return (
            _client.table(_TABLE)
            .select("case_number,defendant_name,property_address,phone,language_hint,"
                    "judgment_date,judgment_against,plaintiff_name,state,county")
            .not_.is_("phone", "null")
            .is_("ghl_contact_id", "null")
            .limit(limit)
            .execute()
            .data or []
        )

    records = await asyncio.to_thread(_fetch)
    log.info("ISTS GHL push: %d enriched-unpushed records (limit=%d)", len(records), limit)

    metrics = {"total": len(records), "pushed": 0, "failed": 0}

    for rec in records:
        contact_id = await push_contact(rec, dry_run=dry_run)
        now = datetime.now(timezone.utc).isoformat()

        if dry_run:
            metrics["pushed"] += 1
            continue

        if contact_id:
            def _mark(case=rec["case_number"], cid=contact_id, t=now):
                _client.table(_TABLE).update({
                    "ghl_contact_id": cid, "ghl_pushed_at": t,
                }).eq("case_number", case).execute()
            await asyncio.to_thread(_mark)
            metrics["pushed"] += 1
        else:
            metrics["failed"] += 1

    return metrics
