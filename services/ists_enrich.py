"""ISTS Sub-Project B — SearchBug enrichment for ists_judgments.

Reads unenriched records (phone IS NULL), calls SearchBug with the full
defendant address, stores phone + language_hint back to ists_judgments.
SELECT-only on filings/lead_contacts; writes only ists_judgments.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone

from dotenv import load_dotenv
from supabase import create_client, Client

from services.searchbug_service import search_tenant_detailed

load_dotenv()

log = logging.getLogger(__name__)

_client: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)

_TABLE = "ists_judgments"

# Strip occupant qualifiers before SearchBug query
_OCCUPANT_RE = re.compile(
    r"\s+(?:and\s+)?all\s+(?:other\s+)?occupants?.*$",
    re.IGNORECASE,
)
# Spanish-surname heuristic: common Latin endings
_SPANISH_SURNAME_RE = re.compile(
    r"(ez|os|as|ia|io|ón|on|ar|er|ado|eda|ero|era|illo|illo|ito|ita|uez|quez|ndo)$",
    re.IGNORECASE,
)


def _clean_name(raw: str) -> str:
    """Strip occupant trailers and normalise."""
    return _OCCUPANT_RE.sub("", raw).strip()


def _split_name(full_name: str) -> tuple[str, str]:
    """Return (first, last). Handles 'Last, First' format from Harris extract."""
    name = _clean_name(full_name).strip()
    if "," in name:
        last, _, first = name.partition(",")
        return first.strip().split()[0], last.strip()
    parts = name.split()
    if len(parts) >= 2:
        return parts[0], parts[-1]
    return name, ""


def _parse_address_parts(address: str) -> tuple[str, str, str]:
    """Return (city, state, zip) from '123 Main St, Houston, TX 77002'."""
    parts = [p.strip() for p in address.split(",")]
    city = parts[1] if len(parts) >= 2 else ""
    state_zip = parts[2].strip() if len(parts) >= 3 else ""
    tokens = state_zip.split()
    state = tokens[0] if tokens else ""
    zip_ = tokens[1] if len(tokens) >= 2 else ""
    return city, state, zip_


def _language_hint(first: str, last: str) -> str:
    """Simple heuristic: 'spanish_likely' if last name ends with common Spanish suffix."""
    if _SPANISH_SURNAME_RE.search(last):
        return "spanish_likely"
    return "english_likely"


async def enrich_batch(limit: int = 50, dry_run: bool = False) -> dict:
    """Enrich up to `limit` unenriched ists_judgments records with SearchBug.

    Returns metrics dict: {total, phone_found, no_records, ambiguous, errors, skipped}
    """
    def _fetch() -> list[dict]:
        return (
            _client.table(_TABLE)
            .select("case_number,defendant_name,property_address,state,county")
            .is_("phone", "null")
            .is_("enriched_at", "null")
            .limit(limit)
            .execute()
            .data or []
        )

    records = await asyncio.to_thread(_fetch)
    log.info("ISTS enrich: %d unenriched records fetched (limit=%d)", len(records), limit)

    metrics = {"total": len(records), "phone_found": 0, "no_records": 0,
               "ambiguous": 0, "errors": 0, "skipped": 0}

    for rec in records:
        case_number = rec["case_number"]
        defendant = rec["defendant_name"]
        address = rec["property_address"]

        first, last = _split_name(defendant)
        if not first or not last:
            log.info("ISTS enrich: skipping %s — bad name %r", case_number, defendant)
            metrics["skipped"] += 1
            continue

        city, state, zip_ = _parse_address_parts(address)
        hint = _language_hint(first, last)

        if dry_run:
            log.info("DRY ENRICH %s | %s %s | %s, %s %s | hint=%s",
                     case_number, first, last, city, state, zip_, hint)
            continue

        result = await search_tenant_detailed(
            first_name=first,
            last_name=last,
            city=city,
            state=state,
            postal=zip_,
            address=address,
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
            log.info("ISTS enrich: phone found %s → %s (%s)", case_number, phone[:4] + "****", result.status)
        elif result.status in ("no_records",):
            metrics["no_records"] += 1
            log.info("ISTS enrich: no record %s (%s %s)", case_number, first, last)
        elif result.status == "ambiguous":
            metrics["ambiguous"] += 1
        else:
            metrics["errors"] += 1
            log.warning("ISTS enrich: %s %s (%s %s)", result.status, case_number, first, last)

    return metrics
