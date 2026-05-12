from __future__ import annotations
import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
import httpx
from supabase import create_client, Client
from models.filing import Filing
from models.contact import EnrichedContact, RoutingOutcome
from pipeline.qualification import QualificationOutcome

load_dotenv()

log = logging.getLogger(__name__)
_SUPABASE_RETRY_ATTEMPTS = 3
_SUPABASE_RETRY_DELAY_SECONDS = 1.0

_client: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)


def _execute_with_retry(query, label: str):
    for attempt in range(1, _SUPABASE_RETRY_ATTEMPTS + 1):
        try:
            return query.execute()
        except httpx.TransportError as exc:
            if attempt == _SUPABASE_RETRY_ATTEMPTS:
                raise
            log.warning(
                "Supabase %s transport error on attempt %s/%s: %s",
                label,
                attempt,
                _SUPABASE_RETRY_ATTEMPTS,
                exc,
            )
            time.sleep(_SUPABASE_RETRY_DELAY_SECONDS * attempt)


def _execute_optional_lead_contact_write(query) -> None:
    try:
        _execute_with_retry(query, "lead_contacts optional write")
    except Exception as exc:
        # Keep legacy filing writes alive while the lead_contacts migration is
        # being applied across environments.
        log.warning("lead_contacts write suppressed: %s", exc)
        return


async def is_duplicate(case_number: str) -> bool:
    def _query() -> bool:
        result = _execute_with_retry(
            _client.table("filings").select("case_number").eq("case_number", case_number),
            "duplicate check",
        )
        return len(result.data) > 0
    return await asyncio.to_thread(_query)


async def insert_filing(filing: Filing) -> None:
    def _insert() -> None:
        _execute_with_retry(
            _client.table("filings").insert({
                "case_number": filing.case_number,
                "tenant_name": filing.tenant_name,
                "property_address": filing.property_address,
                "landlord_name": filing.landlord_name,
                "filing_date": filing.filing_date.isoformat(),
                "court_date": filing.court_date.isoformat() if filing.court_date else None,
                "state": filing.state,
                "county": filing.county,
                "notice_type": filing.notice_type,
                "source_url": filing.source_url,
            }),
            "insert filing",
        )
    await asyncio.to_thread(_insert)


def _enrichment_payload(contact: EnrichedContact) -> dict:
    return {
        "phone": contact.phone,
        "email": contact.email,
        "secondary_address": contact.secondary_address,
        "estimated_rent": contact.estimated_rent,
        "property_type": contact.property_type,
        "dnc_status": contact.dnc_status,
        "dnc_source": contact.dnc_source,
        "dnc_checked_at": datetime.now(timezone.utc).isoformat(),
        "language_hint": contact.language_hint,
    }


def _lead_contact_payload(contact: EnrichedContact) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "case_number": contact.filing.case_number,
        "track": contact.track,
        "contact_name": contact.contact_name,
        "phone": contact.phone,
        "email": contact.email,
        "secondary_address": contact.secondary_address,
        "estimated_rent": contact.estimated_rent,
        "property_type": contact.property_type,
        "dnc_status": contact.dnc_status,
        "dnc_source": contact.dnc_source,
        "dnc_checked_at": now,
        "language_hint": contact.language_hint,
        "enrichment_source": "batchdata",
        "updated_at": now,
    }


def _ng_legacy_enrichment_payload(contact: EnrichedContact) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "ng_dnc_status": contact.dnc_status,
        "ng_dnc_source": contact.dnc_source,
        "ng_dnc_checked_at": now,
        "language_hint": contact.language_hint,
    }


async def upsert_contact_enrichment(contact: EnrichedContact) -> None:
    def _update() -> None:
        _execute_optional_lead_contact_write(
            _client.table("lead_contacts").upsert(
                _lead_contact_payload(contact),
                on_conflict="case_number,track",
            )
        )
        if contact.track == "ec":
            legacy_payload = _enrichment_payload(contact)
        else:
            legacy_payload = _ng_legacy_enrichment_payload(contact)
        _execute_with_retry(
            _client.table("filings").update(legacy_payload).eq(
                "case_number",
                contact.filing.case_number,
            ),
            "update enrichment",
        )
    await asyncio.to_thread(_update)


async def update_enrichment(contact: EnrichedContact) -> None:
    await upsert_contact_enrichment(contact)


async def update_classification(case_number: str, outcome: QualificationOutcome) -> None:
    def _update() -> None:
        _execute_with_retry(
            _client.table("filings").update({
                "property_zip": outcome.property_zip,
                "lead_bucket": outcome.lead_bucket,
                "discard_reason": outcome.discard_reason,
                "qualification_notes": outcome.qualification_notes,
                "classified_at": datetime.now(timezone.utc).isoformat(),
            }).eq("case_number", case_number),
            "update classification",
        )
    await asyncio.to_thread(_update)


async def update_language_hint(case_number: str, language_hint: str | None) -> None:
    def _update() -> None:
        _execute_with_retry(
            _client.table("filings").update({
                "language_hint": language_hint,
            }).eq("case_number", case_number),
            "update language hint",
        )
    await asyncio.to_thread(_update)


def _manual_dnc_payload(source: str, notes: str | None = None) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    clean_source = (source or "manual_review").strip() or "manual_review"
    return {
        "dnc_status": "clear",
        "dnc_source": f"manual_override:{clean_source}",
        "dnc_checked_at": now,
        "dnc_override_source": clean_source,
        "dnc_override_notes": notes,
        "dnc_override_at": now,
    }


async def clear_dnc_status(
    case_number: str,
    track: str = "ec",
    source: str = "manual_review",
    notes: str | None = None,
) -> None:
    payload = _manual_dnc_payload(source=source, notes=notes)
    lead_payload = {
        "dnc_status": "clear",
        "dnc_source": payload["dnc_source"],
        "dnc_checked_at": payload["dnc_checked_at"],
        "updated_at": payload["dnc_checked_at"],
    }
    if track != "ec":
        payload["ng_dnc_status"] = payload.pop("dnc_status")
        payload["ng_dnc_source"] = payload.pop("dnc_source")
        payload["ng_dnc_checked_at"] = payload.pop("dnc_checked_at")

    def _update() -> None:
        _execute_optional_lead_contact_write(
            _client.table("lead_contacts").update(lead_payload).eq(
                "case_number",
                case_number,
            ).eq("track", track)
        )
        _execute_with_retry(
            _client.table("filings").update(payload).eq("case_number", case_number),
            "clear dnc",
        )
    await asyncio.to_thread(_update)


async def update_routing(case_number: str, outcome: RoutingOutcome) -> None:
    def _update() -> None:
        _execute_with_retry(
            _client.table("filings").update({
                "routed": True,
                "routing_outcome": outcome.action,
            }).eq("case_number", case_number),
            "update routing",
        )
    await asyncio.to_thread(_update)


async def update_contact_ghl_id(case_number: str, ghl_contact_id: str, track: str = "ec") -> None:
    column = "ghl_contact_id" if track == "ec" else "ng_ghl_contact_id"
    def _update() -> None:
        _execute_optional_lead_contact_write(
            _client.table("lead_contacts").update({
                "ghl_contact_id": ghl_contact_id,
            }).eq("case_number", case_number).eq("track", track)
        )
        _execute_with_retry(
            _client.table("filings").update({
                column: ghl_contact_id,
            }).eq("case_number", case_number),
            "update ghl id",
        )
    await asyncio.to_thread(_update)


async def update_ghl_id(case_number: str, ghl_contact_id: str, track: str = "ec") -> None:
    await update_contact_ghl_id(case_number, ghl_contact_id, track)


async def mark_bland_triggered(case_number: str, track: str = "ec") -> None:
    column = "bland_triggered" if track == "ec" else "ng_bland_triggered"
    def _update() -> None:
        _execute_with_retry(
            _client.table("filings").update({
                column: True,
            }).eq("case_number", case_number),
            "mark bland triggered",
        )
    await asyncio.to_thread(_update)


async def write_run_metrics(metrics: dict) -> None:
    def _insert() -> None:
        _execute_with_retry(_client.table("run_metrics").insert(metrics), "write run metrics")
    await asyncio.to_thread(_insert)


async def set_bland_status(case_number: str, track: str, status: str, call_id: str | None = None) -> None:
    col_status = "bland_status" if track == "ec" else "ng_bland_status"
    col_call_id = "bland_call_id" if track == "ec" else "ng_bland_call_id"
    def _update() -> None:
        payload: dict = {col_status: status}
        lead_payload: dict = {"bland_status": status}
        if call_id:
            payload[col_call_id] = call_id
            lead_payload["bland_call_id"] = call_id
        _execute_optional_lead_contact_write(
            _client.table("lead_contacts").update(lead_payload).eq(
                "case_number",
                case_number,
            ).eq("track", track)
        )
        _execute_with_retry(
            _client.table("filings").update(payload).eq("case_number", case_number),
            "set bland status",
        )
    await asyncio.to_thread(_update)


_PENDING_FILING_SELECT = (
    "case_number,tenant_name,landlord_name,property_address,"
    "state,county,filing_date,court_date,phone,email,"
    "property_type,dnc_status,dnc_source"
)


def _overlay_contact_rows(
    filing_rows: list[dict],
    contact_rows: list[dict],
    clear_missing_contact: bool = False,
) -> list[dict]:
    contacts_by_case = {row["case_number"]: row for row in contact_rows}
    merged: list[dict] = []
    for filing_row in filing_rows:
        row = dict(filing_row)
        contact_row = contacts_by_case.get(row["case_number"])
        if contact_row:
            row["phone"] = contact_row.get("phone")
            row["email"] = contact_row.get("email")
            row["property_type"] = contact_row.get("property_type") or row.get("property_type")
            row["estimated_rent"] = contact_row.get("estimated_rent") or row.get("estimated_rent")
            row["dnc_status"] = contact_row.get("dnc_status", "unknown")
            row["dnc_source"] = contact_row.get("dnc_source")
            row["language_hint"] = contact_row.get("language_hint") or row.get("language_hint")
            row["bland_status"] = contact_row.get("bland_status")
            row["ghl_contact_id"] = contact_row.get("ghl_contact_id")
        elif clear_missing_contact:
            row["phone"] = None
            row["email"] = None
            row["dnc_status"] = "unknown"
            row["dnc_source"] = None
            row["bland_status"] = None
            row["ghl_contact_id"] = None
        merged.append(row)
    return merged


def _get_pending_leads_from_contacts(track: str, limit: int) -> list[dict]:
    contacts = (
        _client.table("lead_contacts")
        .select(
            "case_number,track,phone,email,property_type,estimated_rent,"
            "dnc_status,dnc_source,language_hint,bland_status,ghl_contact_id"
        )
        .eq("track", track)
        .eq("bland_status", "pending")
        .not_.is_("ghl_contact_id", "null")
        .limit(limit)
        .execute()
        .data
    )
    case_numbers = [row["case_number"] for row in contacts]
    if not case_numbers:
        return []

    filings = (
        _client.table("filings")
        .select(_PENDING_FILING_SELECT)
        .in_("case_number", case_numbers)
        .order("filing_date", desc=True)
        .execute()
        .data
    )
    return _overlay_contact_rows(filings, contacts)


def _get_pending_leads_legacy(track: str, limit: int) -> list[dict]:
    col_status = "bland_status" if track == "ec" else "ng_bland_status"
    col_ghl = "ghl_contact_id" if track == "ec" else "ng_ghl_contact_id"
    return (
        _client.table("filings")
        .select(f"{_PENDING_FILING_SELECT},{col_status},{col_ghl}")
        .eq(col_status, "pending")
        .not_.is_(col_ghl, "null")
        .order("filing_date", desc=True)
        .limit(limit)
        .execute()
        .data
    )


async def get_pending_leads(track: str = "ec", limit: int = 200) -> list[dict]:
    def _query() -> list[dict]:
        try:
            rows = _get_pending_leads_from_contacts(track, limit)
            if rows or track != "ec":
                return rows
            return _get_pending_leads_legacy(track, limit)
        except Exception:
            if track != "ec":
                return []
            return _get_pending_leads_legacy(track, limit)

    return await asyncio.to_thread(_query)


_DASHBOARD_SELECT = (
    "case_number,tenant_name,landlord_name,property_address,"
    "state,county,filing_date,court_date,scraped_at,phone,email,"
    "property_type,estimated_rent,property_zip,lead_bucket,"
    "discard_reason,qualification_notes,dnc_status,dnc_source,language_hint,"
    "bland_status,ghl_contact_id"
)


def _filter_dashboard_query(query, view: str):
    if view in ("ec_residential", "ng_residential"):
        return query.eq("lead_bucket", "residential_approved").or_(
            "language_hint.is.null,language_hint.neq.spanish_likely"
        )
    if view in ("ec_commercial", "ng_commercial"):
        return query.eq("lead_bucket", "commercial").or_(
            "language_hint.is.null,language_hint.neq.spanish_likely"
        )
    if view == "ng_spanish_residential":
        return query.eq("lead_bucket", "residential_approved").eq("language_hint", "spanish_likely")
    if view == "ng_spanish_commercial":
        return query.eq("lead_bucket", "commercial").eq("language_hint", "spanish_likely")
    if view in ("ec_held", "ng_held"):
        return query.eq("lead_bucket", "held")
    if view in ("ec_discarded", "ng_discarded"):
        return query.eq("lead_bucket", "discarded")
    # Legacy fallback — residential approved, non-Spanish
    return query.eq("lead_bucket", "residential_approved").or_(
        "language_hint.is.null,language_hint.neq.spanish_likely"
    )


def _track_for_dashboard_view(view: str) -> str:
    return "ng" if view.startswith("ng_") else "ec"


def _target_metadata(track: str, view: str) -> dict:
    is_spanish = view in {"ng_spanish_residential", "ng_spanish_commercial"}
    if track == "ng":
        return {
            "target_track": "ng",
            "target_brand": "Vantage Defense Group",
            "target_role": "Spanish tenant" if is_spanish else "Tenant",
            "target_phone_label": "Tenant Phone",
            "missing_phone_label": "NO TENANT PHONE",
        }
    return {
        "target_track": "ec",
        "target_brand": "Grant Ellis Group",
        "target_role": "Landlord / owner",
        "target_phone_label": "Landlord Phone",
        "missing_phone_label": "NO LANDLORD PHONE",
    }


def _decorate_dashboard_rows(rows: list[dict], track: str, view: str) -> list[dict]:
    metadata = _target_metadata(track, view)
    return [{**row, **metadata} for row in rows]


def _overlay_dashboard_contact_data(rows: list[dict], track: str) -> list[dict]:
    case_numbers = [row["case_number"] for row in rows]
    if not case_numbers:
        return rows
    contacts = (
        _client.table("lead_contacts")
        .select(
            "case_number,track,phone,email,property_type,estimated_rent,"
            "dnc_status,dnc_source,language_hint,bland_status,ghl_contact_id"
        )
        .eq("track", track)
        .in_("case_number", case_numbers)
        .execute()
        .data
    )
    return _overlay_contact_rows(rows, contacts, clear_missing_contact=track == "ng")


def _get_ng_dashboard_leads(view: str, limit: int) -> list[dict]:
    ng_contacts = (
        _client.table("lead_contacts")
        .select(
            "case_number,track,phone,email,property_type,estimated_rent,"
            "dnc_status,dnc_source,language_hint,bland_status,ghl_contact_id"
        )
        .eq("track", "ng")
        .execute()
        .data
    )
    if not ng_contacts:
        return []
    ng_case_numbers = [row["case_number"] for row in ng_contacts]
    query = _client.table("filings").select(_DASHBOARD_SELECT)
    query = _filter_dashboard_query(query, view)
    query = query.in_("case_number", ng_case_numbers)
    result = (
        query
        .order("court_date", desc=False, nullsfirst=False)
        .order("filing_date", desc=True)
        .limit(limit)
        .execute()
    )
    rows = _overlay_contact_rows(result.data, ng_contacts, clear_missing_contact=False)
    return _decorate_dashboard_rows(rows, "ng", view)


async def get_dashboard_leads(
    view: str = "residential_approved",
    limit: int = 500,
    track: str | None = None,
) -> list[dict]:
    def _query() -> list[dict]:
        effective_track = track or _track_for_dashboard_view(view)
        if effective_track == "ng":
            return _get_ng_dashboard_leads(view, limit)
        # EC: query filings directly, overlay EC contacts
        query = _client.table("filings").select(_DASHBOARD_SELECT)
        query = _filter_dashboard_query(query, view)
        result = (
            query
            .order("court_date", desc=False, nullsfirst=False)
            .order("filing_date", desc=True)
            .limit(limit)
            .execute()
        )
        rows = result.data
        rows = _overlay_dashboard_contact_data(rows, effective_track)
        return _decorate_dashboard_rows(rows, effective_track, view)
    return await asyncio.to_thread(_query)


def _ec_counts_from_rows(rows: list[dict]) -> dict:
    counts = {
        "ec_residential": 0,
        "ec_commercial": 0,
        "ec_held": 0,
        "ec_discarded": 0,
    }
    for row in rows:
        bucket = row.get("lead_bucket")
        spanish = row.get("language_hint") == "spanish_likely"
        if bucket == "residential_approved" and not spanish:
            counts["ec_residential"] += 1
        elif bucket == "commercial" and not spanish:
            counts["ec_commercial"] += 1
        elif bucket == "held":
            counts["ec_held"] += 1
        elif bucket == "discarded":
            counts["ec_discarded"] += 1
    return counts


def _ng_counts_from_contact_rows(rows: list[dict]) -> dict:
    counts = {
        "ng_residential": 0,
        "ng_commercial": 0,
        "ng_spanish_residential": 0,
        "ng_spanish_commercial": 0,
        "ng_held": 0,
        "ng_discarded": 0,
    }
    for row in rows:
        filing = row.get("filings") or {}
        bucket = filing.get("lead_bucket")
        spanish = filing.get("language_hint") == "spanish_likely"
        if bucket == "residential_approved":
            if spanish:
                counts["ng_spanish_residential"] += 1
            else:
                counts["ng_residential"] += 1
        elif bucket == "commercial":
            if spanish:
                counts["ng_spanish_commercial"] += 1
            else:
                counts["ng_commercial"] += 1
        elif bucket == "held":
            counts["ng_held"] += 1
        elif bucket == "discarded":
            counts["ng_discarded"] += 1
    return counts


def _count_filings(bucket: str, spanish: bool | None = None) -> int:
    q = (
        _client.table("filings")
        .select("case_number", count="exact")
        .eq("lead_bucket", bucket)
    )
    if spanish is True:
        q = q.eq("language_hint", "spanish_likely")
    elif spanish is False:
        q = q.or_("language_hint.is.null,language_hint.neq.spanish_likely")
    return q.limit(1).execute().count or 0


async def get_dashboard_counts() -> dict:
    def _query() -> dict:
        ec_counts = {
            "ec_residential": _count_filings("residential_approved", spanish=False),
            "ec_commercial": _count_filings("commercial", spanish=False),
            "ec_held": _count_filings("held"),
            "ec_discarded": _count_filings("discarded"),
        }
        ng_rows = (
            _client.table("lead_contacts")
            .select("case_number,filings(lead_bucket,language_hint)")
            .eq("track", "ng")
            .limit(10000)
            .execute()
            .data
        )
        return {**ec_counts, **_ng_counts_from_contact_rows(ng_rows)}
    return await asyncio.to_thread(_query)


async def get_recent_metrics(limit: int = 10) -> list[dict]:
    def _query() -> list[dict]:
        result = (
            _client.table("run_metrics")
            .select("*")
            .order("run_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data
    return await asyncio.to_thread(_query)
