"""Garnish Proof SearchBug enrichment for garnishment_orders.

Reads unenriched records (phone IS NULL), gated to those still inside the
Claim-of-Exemption window (filing_date >= today - GP_FRESHNESS_DAYS), calls
SearchBug with the debtor's home address, writes phone + language_hint back.
Writes ONLY garnishment_orders. No rent dimension."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv
from supabase import create_client, Client

from pipeline.contract import Business
from services import quota_service
from services.name_utils import clean_tenant_name, parse_name
from services.searchbug_service import search_tenant_detailed

load_dotenv()
log = logging.getLogger(__name__)

# Opt-in per-business budget guard (calendar caps + weekend pause). Off by default.
_QUOTA_GUARD_ENABLED = os.getenv("QUOTA_GUARD_ENABLED", "false").lower() == "true"

_client: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)

_TABLE = "garnishment_orders"
GP_FRESHNESS_DAYS = 30  # vacate window — only enrich judgments still actionable

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
    cutoff = (date.today() - timedelta(days=GP_FRESHNESS_DAYS)).isoformat()

    def _fetch() -> list[dict]:
        return (
            _client.table(_TABLE)
            .select("case_number,debtor_name,debtor_address,state,county")
            .is_("phone", "null")
            .is_("enriched_at", "null")
            .gte("filing_date", cutoff)
            # GP has no amount dimension, so prioritize by writ recency: the
            # freshest garnishment writs (most urgent exemption window) first.
            .order("filing_date", desc=True)
            .limit(limit)
            .execute()
            .data or []
        )

    records = await asyncio.to_thread(_fetch)
    log.info("GP enrich: %d unenriched records fetched (limit=%d)", len(records), limit)

    metrics = {"total": len(records), "phone_found": 0, "no_records": 0,
               "ambiguous": 0, "errors": 0, "skipped": 0}

    for rec in records:
        case_number = rec["case_number"]
        debtor = rec["debtor_name"]
        address = rec["debtor_address"]

        first, last = _split_name(debtor)
        if not first or not last:
            log.info("GP enrich: skipping %s — bad name %r", case_number, debtor)
            metrics["skipped"] += 1
            continue

        city, state, zip_ = _parse_address_parts(address)
        hint = _language_hint(last)

        if dry_run:
            log.info("DRY ENRICH %s | %s %s | %s, %s %s | hint=%s",
                     case_number, first, last, city, state, zip_, hint)
            continue

        # Reserve a GP budget slot before the paid call. Denied => daily cap
        # (paid hits) reached or weekend pause — stop the batch.
        if _QUOTA_GUARD_ENABLED:
            res = await quota_service.try_reserve(Business.GARNISH_PROOF, "searchbug", case_number)
            if not res.granted:
                log.info("GP enrich: budget reached / paused — stopping at %d paid hits",
                         metrics["phone_found"])
                metrics["quota_stopped"] = True
                break

        result = await search_tenant_detailed(
            first_name=first, last_name=last,
            city=city, state=state, postal=zip_, address=address,
        )
        phone = result.phone if result.status in ("phone_found", "name_mismatch") else None
        now = datetime.now(timezone.utc).isoformat()

        # Bill only on a hit: commit on a phone, roll back a no-hit (free) so the
        # budget counts paid leads, not attempts.
        if _QUOTA_GUARD_ENABLED:
            if phone:
                await quota_service.commit(Business.GARNISH_PROOF, "searchbug", case_number)
            else:
                await quota_service.rollback(Business.GARNISH_PROOF, "searchbug", case_number)

        def _update(case=case_number, p=phone, h=hint, t=now):
            payload = {"enriched_at": t, "language_hint": h}
            if p:
                payload["phone"] = p
            _client.table(_TABLE).update(payload).eq("case_number", case).execute()

        await asyncio.to_thread(_update)

        if phone:
            metrics["phone_found"] += 1
            log.info("GP enrich: phone found %s → %s (%s)", case_number, phone[:4] + "****", result.status)
        elif result.status == "no_records":
            metrics["no_records"] += 1
        elif result.status == "ambiguous":
            metrics["ambiguous"] += 1
        else:
            metrics["errors"] += 1
            log.warning("GP enrich: %s %s (%s %s)", result.status, case_number, first, last)

    return metrics
