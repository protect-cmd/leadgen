"""Garnish Proof — GHL contact push for garnishment_orders.

Mirror of services.ists_ghl with Garnish Proof config: separate subaccount
(GHL_GP_*), the garnish-proof-lead routing tag (Jonas routes on it), and the
judgment/vacate framing. Reads enriched records (phone IS NOT NULL,
ghl_contact_id IS NULL), upserts the contact, writes ghl_contact_id back.
Writes only garnishment_orders.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

log = logging.getLogger(__name__)

_TABLE = "garnishment_orders"
_BASE = "https://services.leadconnectorhq.com"
_API_VERSION = "2021-07-28"

# Garnish Proof GHL subaccount — populated once Jonas creates it.
_LOCATION_ID = os.environ.get("GHL_GP_LOCATION_ID", "")
_API_KEY = os.environ.get("GHL_API_GP_KEY", "")
_STAGE_ID = os.environ.get("GHL_GP_NEW_FILING_STAGE_ID", "")

# Custom field UUIDs in the GP subaccount. Empty until Jonas creates the fields
# and pastes their UUIDs here; an empty UUID is silently skipped (push still
# succeeds), so the contact upsert works before the custom fields exist.
_FIELD_IDS: dict[str, str] = {
    "language_preference": "dzCwaXHx2dKXyKSNYfjm",
    "situation": "qfg3kDdz9n9K1x7kWhO0",
    "state": "FDqmMvQQODB1TFFapTFu",
    "county": "TahBtRLIIyanBcElTjeH",
    "case_number": "ITDQZWMA468TLUKKueL4",
    "debtor_name": "pUE3Jjr4gnmClzygfxAw",
    "creditor_name": "cjqkbeNG4izIZ1ntNcCP",
    "judgment_date": "GcCNsQR4Z7pNPfNoh7Bt",
    "vacate_deadline": "jvtvUfWVuT27QKmVHqOz",
}

_client: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)

_pipeline_id_cache: str | None = None

_STATE_MAP = {"TX": "Texas", "AZ": "Arizona", "CA": "California",
              "FL": "Florida", "GA": "Georgia", "NV": "Nevada", "OH": "Ohio"}


def _headers() -> dict:
    if not _API_KEY:
        raise RuntimeError("GHL_API_GP_KEY not set")
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
        first_parts = first.strip().split()
        if first_parts:
            return first_parts[0].title(), last.strip().title()
        return last.strip().title(), ""
    parts = name.split()
    return (parts[0].title(), " ".join(parts[1:]).title()) if len(parts) >= 2 else (name.title(), "")


async def _get_pipeline_id(client: httpx.AsyncClient) -> str | None:
    global _pipeline_id_cache
    if _pipeline_id_cache:
        return _pipeline_id_cache
    r = await client.get(f"{_BASE}/opportunities/pipelines",
                         params={"locationId": _LOCATION_ID}, headers=_headers())
    if r.status_code != 200:
        log.warning("GP GHL: failed to fetch pipelines %s", r.status_code)
        return None
    for pipeline in r.json().get("pipelines", []):
        for stage in pipeline.get("stages", []):
            if stage["id"] == _STAGE_ID:
                _pipeline_id_cache = pipeline["id"]
                return _pipeline_id_cache
    return None


async def push_contact(rec: dict, dry_run: bool = False) -> str | None:
    """Upsert one garnishment_orders record to the GP GHL subaccount."""
    if not _LOCATION_ID or not _API_KEY:
        raise RuntimeError("GHL_GP_LOCATION_ID or GHL_API_GP_KEY not set")

    first, last = _split_name(rec["debtor_name"])
    lang = rec.get("language_hint") or "english_likely"
    lang_value = "Spanish" if lang == "spanish_likely" else "English"
    state_value = _STATE_MAP.get((rec.get("state") or "TX").upper(), "Texas")

    custom_fields: list[dict] = []

    def _add(slug: str, value) -> None:
        fid = _FIELD_IDS.get(slug)
        if fid and value not in (None, ""):
            custom_fields.append({"id": fid, "field_value": value})

    _add("language_preference", lang_value)
    _add("situation", "Default Judgment Entered")
    _add("state", state_value)
    _add("county", rec.get("county"))
    _add("case_number", rec.get("case_number"))
    _add("debtor_name", f"{first} {last}".strip())
    _add("creditor_name", rec.get("creditor_name"))
    _add("judgment_date", str(rec.get("filing_date")) if rec.get("filing_date") else None)
    _add("vacate_deadline", str(rec.get("exemption_deadline")) if rec.get("exemption_deadline") else None)

    payload = {
        "locationId": _LOCATION_ID,
        "firstName": first,
        "lastName": last,
        "phone": rec["phone"],
        "address1": rec.get("debtor_address", ""),
        "tags": ["GarnishProof", "garnish-proof-lead"],
        "source": "Garnish Proof Harris Judgment",
        "customFields": custom_fields,
    }

    if dry_run:
        log.info("DRY GP-GHL %s | %s %s | %s | lang=%s",
                 rec["case_number"], first, last, rec["phone"][:4] + "****", lang_value)
        return "dry-run"

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{_BASE}/contacts/upsert", json=payload, headers=_headers())
        if r.status_code not in (200, 201):
            log.error("GP GHL upsert failed %s: %s", r.status_code, r.text[:300])
            return None

        contact_id: str = r.json().get("contact", {}).get("id", "")
        log.info("GP GHL contact upserted: %s -> %s", rec["case_number"], contact_id)

        note = (
            f"Garnish Proof — Default Judgment Entered\n"
            f"Case: {rec['case_number']}\n"
            f"Judgment Date: {rec.get('filing_date') or 'N/A'}\n"
            f"Creditor: {rec.get('creditor_name') or 'N/A'}\n"
            f"Vacate by: {rec.get('exemption_deadline') or 'N/A'}\n"
            f"Address: {rec.get('debtor_address') or 'N/A'}\n"
            f"Source: Harris JP Civil Extract (Debt Claim)"
        )
        await client.post(f"{_BASE}/contacts/{contact_id}/notes",
                          json={"body": note, "userId": ""}, headers=_headers())

        pipeline_id = await _get_pipeline_id(client)
        if pipeline_id and _STAGE_ID:
            opp_r = await client.post(
                f"{_BASE}/opportunities/",
                json={"locationId": _LOCATION_ID, "contactId": contact_id,
                      "name": f"{rec['case_number']} — {first} {last}",
                      "pipelineId": pipeline_id, "pipelineStageId": _STAGE_ID,
                      "status": "open"},
                headers=_headers())
            if opp_r.status_code in (200, 201):
                log.info("GP GHL opportunity created for %s", contact_id)

    return contact_id


_FRESHNESS_DAYS = 30  # vacate window — only push records within this many days


async def push_batch(limit: int = 50, dry_run: bool = False) -> dict:
    """Push up to `limit` enriched, unpushed GP records to the GP GHL subaccount."""
    cutoff = (date.today() - timedelta(days=_FRESHNESS_DAYS)).isoformat()

    def _fetch() -> list[dict]:
        return (
            _client.table(_TABLE)
            .select("case_number,debtor_name,debtor_address,phone,language_hint,"
                    "filing_date,creditor_name,exemption_deadline,state,county")
            .not_.is_("phone", "null")
            .is_("ghl_contact_id", "null")
            .gte("filing_date", cutoff)
            .limit(limit)
            .execute()
            .data or []
        )

    records = await asyncio.to_thread(_fetch)
    log.info("GP GHL push: %d enriched-unpushed records (limit=%d)", len(records), limit)

    metrics = {"total": len(records), "pushed": 0, "failed": 0}

    for rec in records:
        contact_id = await push_contact(rec, dry_run=dry_run)
        now = datetime.now(timezone.utc).isoformat()
        if dry_run:
            metrics["pushed"] += 1
            continue
        if contact_id:
            def _mark(case=rec["case_number"], cid=contact_id, t=now):
                _client.table(_TABLE).update(
                    {"ghl_contact_id": cid, "ghl_pushed_at": t}
                ).eq("case_number", case).execute()
            await asyncio.to_thread(_mark)
            metrics["pushed"] += 1
        else:
            metrics["failed"] += 1

    return metrics
