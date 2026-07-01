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

_TABLE = "ists_judgments"

# Spanish-surname heuristic: common Latin endings
_SPANISH_SURNAME_RE = re.compile(
    r"(ez|os|as|ia|io|ón|on|ar|er|ado|eda|ero|era|illo|illo|ito|ita|uez|quez|ndo)$",
    re.IGNORECASE,
)


def _split_name(full_name: str) -> tuple[str, str]:
    """Return (first, last) using the shared Vantage-grade parser."""
    cleaned = clean_tenant_name(full_name)
    return parse_name(cleaned)


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


_FRESHNESS_DAYS = 14  # only enrich records within this many days of today


async def enrich_batch(limit: int = 50, dry_run: bool = False,
                       max_found: int | None = None) -> dict:
    """Enrich up to `limit` unenriched ists_judgments records with SearchBug.

    Freshness gate: only processes records where judgment_date >= today - 14 days.
    Stale records are excluded — wasting SearchBug credits on them has no value.

    Returns metrics dict: {total, phone_found, no_records, ambiguous, errors, skipped}
    """
    cutoff = (date.today() - timedelta(days=_FRESHNESS_DAYS)).isoformat()

    def _fetch() -> list[dict]:
        return (
            _client.table(_TABLE)
            .select("case_number,defendant_name,property_address,state,county")
            .is_("phone", "null")
            .is_("enriched_at", "null")
            .gte("judgment_date", cutoff)
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
        rent = None

        try:
            from models.filing import Filing as _F
            from services import rent_estimate_service

            rent = await rent_estimate_service.estimate_rent(
                _F(
                    case_number=case_number,
                    tenant_name=defendant,
                    property_address=address,
                    landlord_name="",
                    filing_date=date.today(),
                    state=state,
                    county=rec["county"],
                    notice_type="Judgment",
                    source_url="",
                )
            )
        except Exception:
            rent = None

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

        # SearchBug credit depletion: the vendor circuit-breaker returns account_error
        # with no charge. STOP before stamping enriched_at — otherwise we burn fresh
        # leads (mark them attempted at 0% hit) and lock them out of future retries.
        if result.status == "account_error":
            log.warning("ISTS enrich: SearchBug account_error (credits depleted) — "
                        "stopping at %d paid hits, not burning remaining leads",
                        metrics["phone_found"])
            metrics["depleted"] = True
            break

        # ists_judgments has no searchbug_status column, so a name_mismatch phone
        # would be indistinguishable from a clean hit and get auto-dialed by
        # ists_bland. Never store it — TCPA/wrong-party hold. (We still paid for it.)
        phone = result.phone if result.status == "phone_found" else None
        now = datetime.now(timezone.utc).isoformat()

        def _update(case=case_number, p=phone, h=hint, t=now, rnt=rent):
            payload = {"enriched_at": t, "language_hint": h}
            if p:
                payload["phone"] = p
            if rnt:
                payload["estimated_rent"] = rnt
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

        if max_found and metrics["phone_found"] >= max_found:
            log.info("ISTS enrich: budget cap reached (%d paid hits)", metrics["phone_found"])
            break

    return metrics
