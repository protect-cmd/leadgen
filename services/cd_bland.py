"""Cosner Drake — Bland.ai voicemail trigger for cosner_filings.

Mirror of services.gp_bland with Cosner Drake config (BLAND_CD_* agent / the
pre-judgment "you've been sued / file your Answer" script). Reuses the shared
DNC service and the ISTS call helpers (phone formatting, call window, name
split). Reads records with phone + ghl_contact_id but no bland_call_id. Writes
only cosner_filings.

Unlike ISTS/GP (which gate on a backward freshness lookback from the judgment),
Cosner Drake is PRE-judgment: the lead is only valuable while the Answer window
is still open. So the gate is forward-looking — answer_deadline must be today or
later. A record whose deadline has passed (or is null) is excluded; once the
window closes the call has no value and could be misleading.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from supabase import create_client, Client

# Reuse the generic Bland helpers from the ISTS module (pure functions).
from services.ists_bland import _spoken_phone, _in_call_window, _split_name

load_dotenv()

log = logging.getLogger(__name__)

_TABLE = "cosner_filings"
_BASE = "https://api.bland.ai"

_CD_AGENT_ID         = os.environ.get("BLAND_CD_AGENT_ID", "")
_CD_SPANISH_AGENT_ID = os.environ.get("BLAND_CD_SPANISH_AGENT_ID", "")
_CD_PHONE_NUMBER     = os.environ.get("BLAND_CD_PHONE_NUMBER", "")
_CD_CALLBACK_NUMBER  = os.environ.get("BLAND_CD_CALLBACK_PHONE_NUMBER", "")
# Persona voices. Marcus = male (pathway default voice is female), Daniel = Spanish male.
_CD_VOICE            = os.environ.get("BLAND_CD_VOICE", "mason")
_CD_SPANISH_VOICE    = os.environ.get("BLAND_CD_SPANISH_VOICE", "Esteban")

_COURT_TZ = ZoneInfo("America/Chicago")  # Harris County, TX

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
    """Dispatch a Cosner Drake voicemail call for one cosner_filings record."""
    is_spanish = (rec.get("language_hint") or "") == "spanish_likely"
    agent_id = _CD_SPANISH_AGENT_ID if is_spanish else _CD_AGENT_ID
    from_number = _CD_PHONE_NUMBER

    if not agent_id:
        var = "BLAND_CD_SPANISH_AGENT_ID" if is_spanish else "BLAND_CD_AGENT_ID"
        log.warning("CD Bland: %s not set — skipping %s", var, rec["case_number"])
        return None
    if not from_number:
        log.warning("CD Bland: BLAND_CD_PHONE_NUMBER not set — skipping %s", rec["case_number"])
        return None

    now_ct = datetime.now(timezone.utc).astimezone(_COURT_TZ)
    if not _in_call_window(now_ct, is_spanish):
        log.info("CD Bland: outside call window (%s CT) — skipping %s",
                 now_ct.strftime("%H:%M"), rec["case_number"])
        return "outside_window"

    first, _ = _split_name(rec["defendant_name"])
    callback = _spoken_phone(_CD_CALLBACK_NUMBER or from_number)
    address = rec.get("defendant_address", "")

    if dry_run:
        log.info("DRY CD-BLAND %s | %s | %s | agent=%s | lang=%s | answer-by=%s",
                 rec["case_number"], first, rec["phone"][:4] + "****",
                 agent_id[:8] + "...", "es" if is_spanish else "en",
                 rec.get("answer_deadline"))
        return "dry-run"

    # DNC compliance gate — shared scrubber, never dial a DNC number.
    from services import dnc_service
    if dnc_service.verdict(rec["phone"]) == "dnc":
        log.info("CD Bland: DNC — skipping %s", rec["case_number"])
        return "dnc_skip"

    payload = {
        "phone_number": rec["phone"],
        "from": from_number,
        "pathway_id": agent_id,
        "voice": _CD_SPANISH_VOICE if is_spanish else _CD_VOICE,
        # Wait for the callee to speak first so the intro isn't half-gone before they
        # get the phone to their ear (agent stays silent until it hears "hello?").
        "wait_for_greeting": True,
        "answered_by_enabled": True,
        "request_data": {
            "first_name": first,
            "county": rec.get("county", ""),
            "property_address": address,
            "answer_deadline": str(rec.get("answer_deadline") or ""),
            "cd_phone": callback,
        },
        "voicemail": {"action": "leave_message", "sensitive": True},
        "record": True,
        "max_duration": 4,
        "metadata": {
            "case_number": rec["case_number"],
            "track": "cosner-drake",
            "stage": "answer_window",
        },
    }

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{_BASE}/v1/calls", json=payload, headers=_headers())

    if r.status_code not in (200, 201):
        log.error("CD Bland dispatch failed %s: %s", r.status_code, r.text[:300])
        return None

    call_id: str = r.json().get("call_id", "")
    log.info("CD Bland call dispatched: call_id=%s case=%s lang=%s",
             call_id, rec["case_number"], "es" if is_spanish else "en")
    return call_id


async def trigger_batch(limit: int = 50, dry_run: bool = False) -> dict:
    """Trigger CD voicemail calls for up to `limit` GHL-pushed, uncalled records.

    Forward-looking freshness gate: only records whose Answer deadline is today or
    later (answer_deadline >= today). Records with a passed or null deadline are
    excluded — the Answer-window pitch only makes sense while the window is open.
    """
    today = date.today().isoformat()

    def _fetch() -> list[dict]:
        return (
            _client.table(_TABLE)
            .select("case_number,defendant_name,phone,language_hint,defendant_address,"
                    "county,filing_date,answer_deadline,ghl_contact_id")
            .not_.is_("phone", "null")
            .not_.is_("ghl_contact_id", "null")
            .is_("bland_call_id", "null")
            .gte("answer_deadline", today)
            .limit(limit)
            .execute()
            .data or []
        )

    records = await asyncio.to_thread(_fetch)
    log.info("CD Bland: %d ready-to-call records (limit=%d)", len(records), limit)

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
            agent = _CD_SPANISH_AGENT_ID if is_spanish else _CD_AGENT_ID
            if not agent or not _CD_PHONE_NUMBER:
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
