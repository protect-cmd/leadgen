"""ISTS Sub-Project B — Bland.ai W1 call trigger for ists_judgments.

Reads records with phone + ghl_contact_id but no bland_call_id.
Dispatches Window 1 call (Marcus=English / Diego=Spanish) via Bland.ai.
Respects time window: 9am–6pm CT (no Sunday before 10am).
Writes only ists_judgments.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

log = logging.getLogger(__name__)

_TABLE = "ists_judgments"
_BASE = "https://api.bland.ai"

# ISTS Bland.ai agents — set in .env after creating pathways in Bland portal
_ISTS_AGENT_ID         = os.environ.get("BLAND_ISTS_AGENT_ID", "")
_ISTS_SPANISH_AGENT_ID = os.environ.get("BLAND_ISTS_SPANISH_AGENT_ID", "")
_ISTS_PHONE_NUMBER     = os.environ.get("BLAND_ISTS_PHONE_NUMBER", "")
_ISTS_CALLBACK_NUMBER  = os.environ.get("BLAND_ISTS_CALLBACK_PHONE_NUMBER", "")

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


def _spoken_phone(number: str) -> str:
    digits = "".join(c for c in number if c.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return number
    return f"{digits[0]}{digits[1]}{digits[2]}, {digits[3]}{digits[4]}{digits[5]}, {digits[6]}{digits[7]}{digits[8]}{digits[9]}"


def _in_call_window(now_ct: datetime, is_spanish: bool = False) -> bool:
    """True if current CT time is within the allowed call window."""
    hour = now_ct.hour
    weekday = now_ct.weekday()  # 0=Mon, 6=Sun
    start = 9 if not is_spanish else 9
    end = 18  # 6pm (W1 window)
    if weekday == 6 and hour < 10:  # Sunday before 10am
        return False
    return start <= hour < end


def _split_name(full_name: str) -> tuple[str, str]:
    import re
    name = re.sub(r"\s+(?:and\s+)?all\s+(?:other\s+)?occupants?.*$", "", full_name,
                  flags=re.IGNORECASE).strip()
    if "," in name:
        last, _, first = name.partition(",")
        return first.strip().split()[0].title(), last.strip().title()
    parts = name.split()
    return (parts[0].title(), " ".join(parts[1:]).title()) if len(parts) >= 2 else (name.title(), "")


async def trigger_call(rec: dict, dry_run: bool = False) -> str | None:
    """Dispatch a W1 Bland.ai call for one ists_judgments record.
    Returns the Bland call_id, or None if skipped/failed.
    """
    is_spanish = (rec.get("language_hint") or "") == "spanish_likely"
    agent_id = _ISTS_SPANISH_AGENT_ID if is_spanish else _ISTS_AGENT_ID
    from_number = _ISTS_PHONE_NUMBER

    if not agent_id:
        var = "BLAND_ISTS_SPANISH_AGENT_ID" if is_spanish else "BLAND_ISTS_AGENT_ID"
        log.warning("ISTS Bland: %s not set — skipping %s", var, rec["case_number"])
        return None

    if not from_number:
        log.warning("ISTS Bland: BLAND_ISTS_PHONE_NUMBER not set — skipping %s", rec["case_number"])
        return None

    now_ct = datetime.now(timezone.utc).astimezone(_COURT_TZ)
    if not _in_call_window(now_ct, is_spanish):
        log.info("ISTS Bland: outside call window (%s CT) — skipping %s",
                 now_ct.strftime("%H:%M"), rec["case_number"])
        return "outside_window"

    first, _ = _split_name(rec["defendant_name"])
    callback = _spoken_phone(_ISTS_CALLBACK_NUMBER or from_number)
    address = rec.get("property_address", "")

    if dry_run:
        log.info("DRY BLAND %s | %s | %s | agent=%s | lang=%s",
                 rec["case_number"], first, rec["phone"][:4] + "****",
                 agent_id[:8] + "...", "es" if is_spanish else "en")
        return "dry-run"

    payload = {
        "phone_number": rec["phone"],
        "from": from_number,
        "pathway_id": agent_id,
        "wait_for_greeting": True,
        "answered_by_enabled": True,
        "request_data": {
            "first_name": first,
            "property_address": address,
            "ists_phone": callback,
        },
        "voicemail": {
            "action": "leave_message",
            "sensitive": True,
        },
        "record": True,
        "max_duration": 4,
        "metadata": {
            "case_number": rec["case_number"],
            "track": "ists",
            "window": "W1",
        },
    }

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{_BASE}/v1/calls", json=payload, headers=_headers())

    if r.status_code not in (200, 201):
        log.error("ISTS Bland dispatch failed %s: %s", r.status_code, r.text[:300])
        return None

    call_id: str = r.json().get("call_id", "")
    log.info("ISTS Bland W1 call dispatched: call_id=%s case=%s lang=%s",
             call_id, rec["case_number"], "es" if is_spanish else "en")
    return call_id


async def trigger_batch(limit: int = 50, dry_run: bool = False) -> dict:
    """Trigger W1 Bland calls for up to `limit` GHL-pushed, uncalled records."""
    def _fetch() -> list[dict]:
        return (
            _client.table(_TABLE)
            .select("case_number,defendant_name,phone,language_hint,property_address,"
                    "judgment_date,ghl_contact_id")
            .not_.is_("phone", "null")
            .not_.is_("ghl_contact_id", "null")
            .is_("bland_call_id", "null")
            .limit(limit)
            .execute()
            .data or []
        )

    records = await asyncio.to_thread(_fetch)
    log.info("ISTS Bland: %d ready-to-call records (limit=%d)", len(records), limit)

    metrics = {"total": len(records), "dispatched": 0, "skipped_window": 0,
               "no_agent": 0, "failed": 0}

    for rec in records:
        call_id = await trigger_call(rec, dry_run=dry_run)
        now = datetime.now(timezone.utc).isoformat()

        if call_id == "outside_window":
            metrics["skipped_window"] += 1
            continue
        if call_id is None:
            # Check if it was a missing agent (no_agent) vs API failure
            is_spanish = (rec.get("language_hint") or "") == "spanish_likely"
            agent = _ISTS_SPANISH_AGENT_ID if is_spanish else _ISTS_AGENT_ID
            if not agent or not _ISTS_PHONE_NUMBER:
                metrics["no_agent"] += 1
            else:
                metrics["failed"] += 1
            continue

        if not dry_run and call_id not in ("dry-run", "outside_window"):
            def _mark(case=rec["case_number"], cid=call_id, t=now):
                _client.table(_TABLE).update({
                    "bland_call_id": cid,
                    "bland_triggered_at": t,
                }).eq("case_number", case).execute()
            await asyncio.to_thread(_mark)

        metrics["dispatched"] += 1

    return metrics
