"""Cosner Drake — GHL contact push for cosner_filings.

Reads enriched filings (phone IS NOT NULL, ghl_contact_id IS NULL), upserts a
contact into the Cosner Drake GHL subaccount, creates an opportunity in the
"Cosner Drake Pipeline / New Lead" stage, and writes ghl_contact_id +
ghl_pushed_at back. Writes only cosner_filings; never touches filings /
lead_contacts / ists_judgments / garnishment_orders.

Custom-field IDs + pipeline/stage IDs discovered live from the CD subaccount
(location QrrCcy68dwSuHJeiEG6Z) on 2026-06-24. The `situation` single-option
field is intentionally NOT set: its only option ("Default Judgment Entered") is
post-judgment (Garnish Proof's stage), wrong for these pre-judgment Answer-window
leads. Add a "Debt Claim Filed - Answer Window" option in the GHL UI to enable it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

log = logging.getLogger(__name__)

_TABLE = "cosner_filings"
_BASE = "https://services.leadconnectorhq.com"
_API_VERSION = "2021-07-28"

# Cosner Drake GHL subaccount — discovered live 2026-06-24
_LOCATION_ID = os.environ.get("GHL_CD_LOCATION_ID", "")
_API_KEY = os.environ.get("GHL_API_CD_KEY", "")
# "Cosner Drake Pipeline" -> "New Lead"
_STAGE_ID = os.environ.get("GHL_CD_NEW_LEAD_STAGE_ID", "")

# Custom field UUIDs in the CD subaccount (verified via API 2026-06-24)
_FIELD_IDS = {
    "debtor_name":         "fsNARt5gi7ht6tXwwnXl",
    "case_number":         "nBWx9hxRlUyPhml0aeTp",
    "creditor":            "LIYwjXtGYXgeRfEstveS",
    "answer_deadline":     "ehQ0Nri69GQWypbk9VeX",
    "language_preference": "sEZxcYdov9uxkZp3Lh1l",
}

_client: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)

_pipeline_id_cache: str | None = None


def _headers() -> dict:
    if not _API_KEY:
        raise RuntimeError("GHL_API_CD_KEY not set")
    return {
        "Authorization": f"Bearer {_API_KEY}",
        "Content-Type": "application/json",
        "Version": _API_VERSION,
    }


def _split_name(full_name: str) -> tuple[str, str]:
    name = (full_name or "").strip()
    if "," in name:
        last, _, first = name.partition(",")
        first_parts = first.strip().split()
        first_name = first_parts[0].title() if first_parts else ""
        return first_name, last.strip().title()
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
        log.warning("CD GHL: failed to fetch pipelines %s", r.status_code)
        return None
    for pipeline in r.json().get("pipelines", []):
        for stage in pipeline.get("stages", []):
            if stage["id"] == _STAGE_ID:
                _pipeline_id_cache = pipeline["id"]
                return _pipeline_id_cache
    return None


def _build_payload(rec: dict) -> dict:
    first, last = _split_name(rec["defendant_name"])
    lang = rec.get("language_hint") or "english_likely"
    lang_value = "Spanish" if lang == "spanish_likely" else "English"

    custom_fields: list[dict] = []

    def _add(slug: str, value) -> None:
        if value not in (None, ""):
            custom_fields.append({"id": _FIELD_IDS[slug], "field_value": value})

    _add("debtor_name", rec.get("defendant_name"))
    _add("case_number", rec.get("case_number"))
    _add("creditor", rec.get("creditor_name"))
    _add("answer_deadline", str(rec["answer_deadline"]) if rec.get("answer_deadline") else None)
    _add("language_preference", lang_value)

    return {
        "locationId": _LOCATION_ID,
        "firstName": first,
        "lastName": last,
        "phone": rec["phone"],
        "address1": rec.get("defendant_address", ""),
        "tags": ["Cosner Drake", "cosner-drake-lead", "debt-claim"],
        "source": "Cosner Drake - Harris Debt Claim",
        "customFields": custom_fields,
    }


def _note_body(rec: dict) -> str:
    return (
        "Cosner Drake - Debt Claim Filed (Answer window open)\n"
        f"Case: {rec.get('case_number')}\n"
        f"Creditor: {rec.get('creditor_name') or 'N/A'}\n"
        f"Filed: {rec.get('filing_date') or 'N/A'}\n"
        f"Answer deadline: {rec.get('answer_deadline') or 'N/A'}\n"
        f"Address: {rec.get('defendant_address') or 'N/A'}\n"
        f"County: {rec.get('county') or 'Harris'}, {rec.get('state') or 'TX'}\n"
        "Source: Harris JP Cases Filed extract"
    )


async def push_contact(rec: dict, dry_run: bool = False) -> str | None:
    """Upsert one cosner_filings record to the CD GHL subaccount.
    Returns the GHL contact ID or None on failure."""
    if not _LOCATION_ID or not _API_KEY:
        raise RuntimeError("GHL_CD_LOCATION_ID or GHL_API_CD_KEY not set")

    payload = _build_payload(rec)
    first, last = payload["firstName"], payload["lastName"]

    if dry_run:
        log.info("DRY GHL %s | %s %s | %s | creditor=%s | answer-by=%s",
                 rec.get("case_number"), first, last,
                 (rec.get("phone") or "")[:4] + "****",
                 rec.get("creditor_name"), rec.get("answer_deadline"))
        return "dry-run"

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{_BASE}/contacts/upsert", json=payload, headers=_headers())
        if r.status_code not in (200, 201):
            log.error("CD GHL upsert failed %s: %s", r.status_code, r.text[:300])
            return None

        contact_id: str = r.json().get("contact", {}).get("id", "")
        log.info("CD GHL contact upserted: %s -> %s", rec.get("case_number"), contact_id)

        await client.post(
            f"{_BASE}/contacts/{contact_id}/notes",
            json={"body": _note_body(rec), "userId": ""},
            headers=_headers(),
        )

        pipeline_id = await _get_pipeline_id(client)
        if pipeline_id and _STAGE_ID:
            opp_r = await client.post(
                f"{_BASE}/opportunities/",
                json={
                    "locationId": _LOCATION_ID,
                    "contactId": contact_id,
                    "name": f"{rec.get('case_number')} - {first} {last}",
                    "pipelineId": pipeline_id,
                    "pipelineStageId": _STAGE_ID,
                    "status": "open",
                },
                headers=_headers(),
            )
            if opp_r.status_code in (200, 201):
                log.info("CD GHL opportunity created for %s", contact_id)
            else:
                log.warning("CD GHL opportunity failed %s: %s", opp_r.status_code, opp_r.text[:200])

    return contact_id


async def push_batch(limit: int = 50, dry_run: bool = False) -> dict:
    """Push up to `limit` enriched, unpushed CD filings to the GHL subaccount."""
    def _fetch() -> list[dict]:
        return (
            _client.table(_TABLE)
            .select("case_number,defendant_name,defendant_address,creditor_name,phone,"
                    "language_hint,filing_date,answer_deadline,state,county")
            .not_.is_("phone", "null")
            .is_("ghl_contact_id", "null")
            .limit(limit)
            .execute()
            .data or []
        )

    records = await asyncio.to_thread(_fetch)
    log.info("CD GHL push: %d enriched-unpushed filings (limit=%d)", len(records), limit)

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
