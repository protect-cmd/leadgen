"""Garnish Proof — Bland.ai voicemail trigger for garnishment_orders.

Mirror of services.ists_bland with Garnish Proof config (BLAND_GP_* agent /
"Alex" judgment-vacate script). Reuses the shared DNC service and the ISTS
call helpers (phone formatting, call window, name split). Reads records with
phone + ghl_contact_id but no bland_call_id. Writes only garnishment_orders.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from supabase import create_client, Client

# Reuse the generic Bland helpers from the ISTS module (pure functions).
from services.ists_bland import _spoken_phone, _in_call_window, _split_name

load_dotenv()

log = logging.getLogger(__name__)

_TABLE = "garnishment_orders"
_BASE = "https://api.bland.ai"

_GP_AGENT_ID         = os.environ.get("BLAND_GP_AGENT_ID", "")
_GP_SPANISH_AGENT_ID = os.environ.get("BLAND_GP_SPANISH_AGENT_ID", "")
_GP_PHONE_NUMBER     = os.environ.get("BLAND_GP_PHONE_NUMBER", "")
_GP_CALLBACK_NUMBER  = os.environ.get("BLAND_GP_CALLBACK_PHONE_NUMBER", "")
# Persona voices. Marcus = male (the pathway default voice is female), Daniel = Spanish male.
_GP_VOICE            = os.environ.get("BLAND_GP_VOICE", "mason")
_GP_SPANISH_VOICE    = os.environ.get("BLAND_GP_SPANISH_VOICE", "Esteban")

_COURT_TZ = ZoneInfo("America/Chicago")

_client: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)


def _headers() -> dict:
    key = os.environ.get("BLAND_API_KEY", "")
    if not key:
        raise RuntimeError("BLAND_API_KEY not set")
    return {"authorization": key, "Content-Type": "application/json"}


async def trigger_call(rec: dict, dry_run: bool = False) -> str | None:
    """Dispatch a Garnish Proof voicemail call for one garnishment_orders record."""
    is_spanish = (rec.get("language_hint") or "") == "spanish_likely"
    agent_id = _GP_SPANISH_AGENT_ID if is_spanish else _GP_AGENT_ID
    from_number = _GP_PHONE_NUMBER

    if not agent_id:
        var = "BLAND_GP_SPANISH_AGENT_ID" if is_spanish else "BLAND_GP_AGENT_ID"
        log.warning("GP Bland: %s not set — skipping %s", var, rec["case_number"])
        return None
    if not from_number:
        log.warning("GP Bland: BLAND_GP_PHONE_NUMBER not set — skipping %s", rec["case_number"])
        return None

    now_ct = datetime.now(timezone.utc).astimezone(_COURT_TZ)
    if not _in_call_window(now_ct, is_spanish):
        log.info("GP Bland: outside call window (%s CT) — skipping %s",
                 now_ct.strftime("%H:%M"), rec["case_number"])
        return "outside_window"

    first, _ = _split_name(rec["debtor_name"])
    callback = _spoken_phone(_GP_CALLBACK_NUMBER or from_number)
    address = rec.get("debtor_address", "")

    if dry_run:
        log.info("DRY GP-BLAND %s | %s | %s | agent=%s | lang=%s",
                 rec["case_number"], first, rec["phone"][:4] + "****",
                 agent_id[:8] + "...", "es" if is_spanish else "en")
        return "dry-run"

    # DNC compliance gate — shared scrubber, never dial a DNC number.
    from services import dnc_service
    if dnc_service.verdict(rec["phone"]) == "dnc":
        log.info("GP Bland: DNC — skipping %s", rec["case_number"])
        return "dnc_skip"

    payload = {
        "phone_number": rec["phone"],
        "from": from_number,
        "pathway_id": agent_id,
        "voice": _GP_SPANISH_VOICE if is_spanish else _GP_VOICE,
        # Wait for the callee to speak first so the intro isn't half-gone before they
        # get the phone to their ear (agent stays silent until it hears "hello?").
        "wait_for_greeting": True,
        "answered_by_enabled": True,
        "request_data": {
            "first_name": first,
            "county": rec.get("county", ""),
            "property_address": address,
            "gp_phone": callback,
        },
        "voicemail": {"action": "leave_message", "sensitive": True},
        "record": True,
        "max_duration": 4,
        "metadata": {
            "case_number": rec["case_number"],
            "track": "garnish-proof",
            "stage": "default_judgment",
        },
    }

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{_BASE}/v1/calls", json=payload, headers=_headers())

    if r.status_code not in (200, 201):
        log.error("GP Bland dispatch failed %s: %s", r.status_code, r.text[:300])
        return None

    call_id: str = r.json().get("call_id", "")
    log.info("GP Bland call dispatched: call_id=%s case=%s lang=%s",
             call_id, rec["case_number"], "es" if is_spanish else "en")
    return call_id


_FRESHNESS_DAYS = 30  # vacate window — only call records within this many days


async def trigger_batch(limit: int = 50, dry_run: bool = False) -> dict:
    """Trigger GP voicemail calls for up to `limit` GHL-pushed, uncalled records."""
    cutoff = (date.today() - timedelta(days=_FRESHNESS_DAYS)).isoformat()

    def _fetch() -> list[dict]:
        return (
            _client.table(_TABLE)
            .select("case_number,debtor_name,phone,language_hint,debtor_address,"
                    "county,filing_date,ghl_contact_id")
            .not_.is_("phone", "null")
            .not_.is_("ghl_contact_id", "null")
            .is_("bland_call_id", "null")
            .gte("filing_date", cutoff)
            .limit(limit)
            .execute()
            .data or []
        )

    records = await asyncio.to_thread(_fetch)
    log.info("GP Bland: %d ready-to-call records (limit=%d)", len(records), limit)

    metrics = {"total": len(records), "dispatched": 0, "skipped_window": 0,
               "skipped_dnc": 0, "no_agent": 0, "failed": 0}

    for rec in records:
        call_id = await trigger_call(rec, dry_run=dry_run)
        now = datetime.now(timezone.utc).isoformat()

        if call_id == "outside_window":
            metrics["skipped_window"] += 1
            continue
        if call_id == "dnc_skip":
            metrics["skipped_dnc"] += 1
            continue
        if call_id is None:
            is_spanish = (rec.get("language_hint") or "") == "spanish_likely"
            agent = _GP_SPANISH_AGENT_ID if is_spanish else _GP_AGENT_ID
            if not agent or not _GP_PHONE_NUMBER:
                metrics["no_agent"] += 1
            else:
                metrics["failed"] += 1
            continue

        if not dry_run and call_id not in ("dry-run", "outside_window"):
            def _mark(case=rec["case_number"], cid=call_id, t=now):
                _client.table(_TABLE).update(
                    {"bland_call_id": cid, "bland_triggered_at": t}
                ).eq("case_number", case).execute()
            await asyncio.to_thread(_mark)

        metrics["dispatched"] += 1

    return metrics
