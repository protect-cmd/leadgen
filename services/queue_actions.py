"""Selected-row paid actions for the lead queue dashboard.

These functions intentionally stop at enrichment/rent persistence. They do not
stage GHL contacts or trigger Bland calls; firing remains a separate explicit
dashboard action.
"""
from __future__ import annotations

import asyncio
import os
from collections import Counter
from datetime import date, datetime, timezone

from models.filing import Filing


def limited_case_numbers(case_numbers: list | None, cap: int) -> tuple[list[str], bool]:
    """Return sanitized unique case numbers capped for paid actions."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in case_numbers or []:
        case_number = str(raw or "").strip()
        if not case_number or case_number in seen:
            continue
        seen.add(case_number)
        out.append(case_number)
    return out[:cap], len(out) > cap


def _d(value) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return date.fromisoformat(value[:10])
    return date.today()


def _filing_from_vantage(row: dict) -> Filing:
    return Filing(
        case_number=row["case_number"],
        tenant_name=row.get("tenant_name") or "",
        property_address=row.get("property_address") or "",
        landlord_name=row.get("landlord_name") or "",
        filing_date=_d(row.get("filing_date")),
        court_date=_d(row["court_date"]) if row.get("court_date") else None,
        state=row.get("state") or "",
        county=row.get("county") or "",
        notice_type=row.get("notice_type") or "Eviction",
        source_url=row.get("source_url") or "",
        claim_amount=row.get("estimated_rent"),
        property_type_hint=row.get("property_type"),
    )


def _filing_from_ists(row: dict) -> Filing:
    return Filing(
        case_number=row["case_number"],
        tenant_name=row.get("defendant_name") or "",
        property_address=row.get("property_address") or "",
        landlord_name="",
        filing_date=_d(row.get("judgment_date")),
        state=row.get("state") or "",
        county=row.get("county") or "",
        notice_type="Judgment",
        source_url="",
        claim_amount=row.get("estimated_rent"),
    )


def _summary(results: list[dict]) -> dict:
    return dict(Counter(r["status"] for r in results))


def _fetch_one(sb, table: str, case_number: str, select: str) -> dict | None:
    rows = (
        sb.table(table)
        .select(select)
        .eq("case_number", case_number)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


def _rent_preflight_status() -> str | None:
    from services import rent_estimate_service

    if not rent_estimate_service.is_enabled():
        return "rent_disabled"
    provider = os.getenv("RENT_PRECHECK_PROVIDER", "rentometer").strip().lower()
    if provider != "rentometer":
        return "rent_provider_unsupported"
    if not os.getenv("RENTOMETER_API_KEY", "").strip():
        return "rent_key_missing"
    return None


def _update_dnc_status(sb, table: str, case_number: str, phone: str, track: str | None = None) -> str:
    from services import dnc_service

    verdict = dnc_service.verdict(phone)
    query = sb.table(table).update({"dnc_status": verdict}).eq("case_number", case_number)
    if track:
        query = query.eq("track", track)
    try:
        query.execute()
    except Exception:
        # The action still enriched the phone; older environments may not have
        # dnc_status yet. Fire-time DNC remains enforced by fire_service.
        pass
    return verdict


async def rent_case(sb, case_number: str, *, track: str) -> dict:
    from services import rent_estimate_service

    table = "ists_judgments" if track == "ists" else "filings"
    select = (
        "case_number,defendant_name,property_address,judgment_date,state,county,estimated_rent"
        if track == "ists"
        else "case_number,tenant_name,landlord_name,property_address,filing_date,court_date,"
             "state,county,notice_type,source_url,estimated_rent,property_type"
    )
    row = await asyncio.to_thread(_fetch_one, sb, table, case_number, select)
    if not row:
        return {"case_number": case_number, "status": "no_record"}
    if row.get("estimated_rent"):
        return {"case_number": case_number, "status": "already_has_rent", "rent": row.get("estimated_rent")}
    if not row.get("property_address"):
        return {"case_number": case_number, "status": "missing_address"}
    unavailable = _rent_preflight_status()
    if unavailable:
        return {"case_number": case_number, "status": unavailable}

    filing = _filing_from_ists(row) if track == "ists" else _filing_from_vantage(row)
    rent = await rent_estimate_service.estimate_rent(filing)
    if rent is None:
        return {"case_number": case_number, "status": "no_rent"}

    def _store() -> None:
        sb.table(table).update({"estimated_rent": rent}).eq("case_number", case_number).execute()

    await asyncio.to_thread(_store)
    return {"case_number": case_number, "status": "rent_found", "rent": rent}


async def rent_cases_track(sb, case_numbers: list[str], *, track: str, cap: int = 50) -> dict:
    cases, capped = limited_case_numbers(case_numbers, cap)
    results = [await rent_case(sb, cn, track=track) for cn in cases]
    return {"results": results, "summary": _summary(results), "capped": capped}


async def enrich_vantage_case(sb, case_number: str) -> dict:
    from services import batchdata_service, dedup_service, language_service
    from services.name_utils import infer_property_type

    existing = (
        sb.table("lead_contacts")
        .select("phone")
        .eq("case_number", case_number)
        .eq("track", "ng")
        .not_.is_("phone", "null")
        .limit(1)
        .execute()
        .data
        or []
    )
    if existing:
        return {"case_number": case_number, "status": "already_has_phone"}

    row = await asyncio.to_thread(
        _fetch_one,
        sb,
        "filings",
        case_number,
        "case_number,tenant_name,landlord_name,property_address,filing_date,court_date,"
        "state,county,notice_type,source_url,estimated_rent,property_type,language_hint",
    )
    if not row:
        return {"case_number": case_number, "status": "no_record"}

    filing = _filing_from_vantage(row)
    if filing.property_type_hint is None:
        filing.property_type_hint = infer_property_type(filing)
    contact = await batchdata_service.enrich_tenant(
        filing,
        property_info=None,
        lookup_property_if_missing=False,
    )
    contact.language_hint = row.get("language_hint") or language_service.language_hint_for_name(filing.tenant_name)
    await dedup_service.update_enrichment(contact)

    dnc_status = None
    if contact.phone:
        dnc_status = await asyncio.to_thread(
            _update_dnc_status,
            sb,
            "lead_contacts",
            case_number,
            contact.phone,
            "ng",
        )

    # searchbug_status is None only when the cost gates declined to call
    # SearchBug at all (common surname w/o narrowing address, daily cap hit,
    # circuit breaker, unparseable name). Report that as "skipped" so an
    # un-queried lead is never mistaken for one SearchBug confirmed has no phone.
    if contact.searchbug_status:
        status = contact.searchbug_status
    elif contact.phone:
        status = "phone_found"
    else:
        status = "skipped"
    return {
        "case_number": case_number,
        "status": status,
        "phone_found": bool(contact.phone),
        "dnc_status": dnc_status,
    }


async def enrich_ists_case(sb, case_number: str) -> dict:
    from services import dnc_service
    from services.ists_enrich import _language_hint, _parse_address_parts, _split_name
    from services.searchbug_service import search_tenant_detailed

    row = await asyncio.to_thread(
        _fetch_one,
        sb,
        "ists_judgments",
        case_number,
        "case_number,defendant_name,property_address,state,county,phone",
    )
    if not row:
        return {"case_number": case_number, "status": "no_record"}
    if row.get("phone"):
        return {"case_number": case_number, "status": "already_has_phone"}

    first, last = _split_name(row.get("defendant_name") or "")
    if not first or not last:
        return {"case_number": case_number, "status": "invalid_name"}
    city, state, zip_ = _parse_address_parts(row.get("property_address") or "")
    hint = _language_hint(first, last)
    result = await search_tenant_detailed(
        first_name=first,
        last_name=last,
        city=city,
        state=state or row.get("state") or "",
        postal=zip_,
        address=row.get("property_address") or "",
    )
    phone = result.phone if result.status == "phone_found" else None
    dnc_status = dnc_service.verdict(phone) if phone else None
    payload = {
        "enriched_at": datetime.now(timezone.utc).isoformat(),
        "language_hint": hint,
    }
    if phone:
        payload["phone"] = phone
        payload["dnc_status"] = dnc_status

    def _store() -> None:
        try:
            sb.table("ists_judgments").update(payload).eq("case_number", case_number).execute()
        except Exception:
            payload.pop("dnc_status", None)
            sb.table("ists_judgments").update(payload).eq("case_number", case_number).execute()

    await asyncio.to_thread(_store)
    return {
        "case_number": case_number,
        "status": result.status,
        "phone_found": bool(phone),
        "dnc_status": dnc_status,
    }


async def enrich_cases_track(sb, case_numbers: list[str], *, track: str, cap: int = 25) -> dict:
    cases, capped = limited_case_numbers(case_numbers, cap)
    fn = enrich_ists_case if track == "ists" else enrich_vantage_case
    results = [await fn(sb, cn) for cn in cases]
    return {"results": results, "summary": _summary(results), "capped": capped}
