"""Cosner Drake SearchBug enrichment for cosner_filings.

Reads unenriched filings (phone IS NULL, enriched_at IS NULL), gated to those
still inside the Answer window (filing_date >= today - CD_FRESHNESS_DAYS), calls
SearchBug with the defendant's home address, writes phone + language_hint back.
Writes ONLY cosner_filings. No rent dimension."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv
from supabase import create_client, Client

from services.name_utils import clean_tenant_name, parse_name
from services.searchbug_service import search_tenant_detailed

load_dotenv()
log = logging.getLogger(__name__)

_client: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)

_TABLE = "cosner_filings"
# Answer window — only enrich filings the consumer can still respond to. A
# defendant sued today has ~30 days to file a written Answer before default.
CD_FRESHNESS_DAYS = 30

_SPANISH_SURNAME_RE = re.compile(
    r"(ez|os|as|ia|io|ón|on|ar|er|ado|eda|ero|era|illo|ito|ita|uez|quez|ndo)$",
    re.IGNORECASE,
)


def _split_name(full_name: str) -> tuple[str, str]:
    return parse_name(clean_tenant_name(full_name))


def _parse_address_parts(address: str) -> tuple[str, str, str]:
    parts = [p.strip() for p in address.split(",")]
    city = parts[1] if len(parts) >= 2 else ""
    state_zip = parts[2].strip() if len(parts) >= 3 else ""
    tokens = state_zip.split()
    state = tokens[0] if tokens else ""
    zip_ = tokens[1] if len(tokens) >= 2 else ""
    return city, state, zip_


def _language_hint(last: str) -> str:
    return "spanish_likely" if _SPANISH_SURNAME_RE.search(last) else "english_likely"


async def enrich_batch(limit: int = 50, dry_run: bool = False) -> dict:
    cutoff = (date.today() - timedelta(days=CD_FRESHNESS_DAYS)).isoformat()

    def _fetch() -> list[dict]:
        return (
            _client.table(_TABLE)
            .select("case_number,defendant_name,defendant_address,state,county")
            .is_("phone", "null")
            .is_("enriched_at", "null")
            .gte("filing_date", cutoff)
            # Cosner is value-first: spend the daily cap on the largest debts
            # first (highest-stakes defendants). Unknown amounts sort last.
            .order("debt_amount", desc=True, nullsfirst=False)
            .limit(limit)
            .execute()
            .data or []
        )

    records = await asyncio.to_thread(_fetch)
    log.info("CD enrich: %d unenriched filings fetched (limit=%d)", len(records), limit)

    metrics = {"total": len(records), "phone_found": 0, "no_records": 0,
               "ambiguous": 0, "errors": 0, "skipped": 0}

    for rec in records:
        case_number = rec["case_number"]
        defendant = rec["defendant_name"]
        address = rec["defendant_address"]

        first, last = _split_name(defendant)
        if not first or not last:
            log.info("CD enrich: skipping %s — bad name %r", case_number, defendant)
            metrics["skipped"] += 1
            continue

        city, state, zip_ = _parse_address_parts(address)
        hint = _language_hint(last)

        if dry_run:
            log.info("DRY ENRICH %s | %s %s | %s, %s %s | hint=%s",
                     case_number, first, last, city, state, zip_, hint)
            continue

        result = await search_tenant_detailed(
            first_name=first, last_name=last,
            city=city, state=state, postal=zip_, address=address,
        )
        phone = result.phone if result.status in ("phone_found", "name_mismatch") else None
        now = datetime.now(timezone.utc).isoformat()

        def _update(case=case_number, p=phone, h=hint, t=now):
            payload = {"enriched_at": t, "language_hint": h}
            if p:
                payload["phone"] = p
            _client.table(_TABLE).update(payload).eq("case_number", case).execute()

        await asyncio.to_thread(_update)

        if phone:
            metrics["phone_found"] += 1
            log.info("CD enrich: phone found %s → %s (%s)", case_number, phone[:4] + "****", result.status)
        elif result.status == "no_records":
            metrics["no_records"] += 1
        elif result.status == "ambiguous":
            metrics["ambiguous"] += 1
        else:
            metrics["errors"] += 1
            log.warning("CD enrich: %s %s (%s %s)", result.status, case_number, first, last)

    return metrics
