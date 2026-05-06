from __future__ import annotations
import asyncio
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import create_client, Client
from models.filing import Filing
from models.contact import EnrichedContact, RoutingOutcome
from pipeline.qualification import QualificationOutcome

load_dotenv()

_client: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)


async def is_duplicate(case_number: str) -> bool:
    def _query() -> bool:
        result = _client.table("filings").select("case_number").eq("case_number", case_number).execute()
        return len(result.data) > 0
    return await asyncio.to_thread(_query)


async def insert_filing(filing: Filing) -> None:
    def _insert() -> None:
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
        }).execute()
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


async def update_enrichment(contact: EnrichedContact) -> None:
    def _update() -> None:
        _client.table("filings").update(_enrichment_payload(contact)).eq("case_number", contact.filing.case_number).execute()
    await asyncio.to_thread(_update)


async def update_classification(case_number: str, outcome: QualificationOutcome) -> None:
    def _update() -> None:
        _client.table("filings").update({
            "property_zip": outcome.property_zip,
            "lead_bucket": outcome.lead_bucket,
            "discard_reason": outcome.discard_reason,
            "qualification_notes": outcome.qualification_notes,
            "classified_at": datetime.now(timezone.utc).isoformat(),
        }).eq("case_number", case_number).execute()
    await asyncio.to_thread(_update)


async def update_language_hint(case_number: str, language_hint: str | None) -> None:
    def _update() -> None:
        _client.table("filings").update({
            "language_hint": language_hint,
        }).eq("case_number", case_number).execute()
    await asyncio.to_thread(_update)


async def update_routing(case_number: str, outcome: RoutingOutcome) -> None:
    def _update() -> None:
        _client.table("filings").update({
            "routed": True,
            "routing_outcome": outcome.action,
        }).eq("case_number", case_number).execute()
    await asyncio.to_thread(_update)


async def update_ghl_id(case_number: str, ghl_contact_id: str, track: str = "ec") -> None:
    column = "ghl_contact_id" if track == "ec" else "ng_ghl_contact_id"
    def _update() -> None:
        _client.table("filings").update({
            column: ghl_contact_id,
        }).eq("case_number", case_number).execute()
    await asyncio.to_thread(_update)


async def mark_bland_triggered(case_number: str, track: str = "ec") -> None:
    column = "bland_triggered" if track == "ec" else "ng_bland_triggered"
    def _update() -> None:
        _client.table("filings").update({
            column: True,
        }).eq("case_number", case_number).execute()
    await asyncio.to_thread(_update)


async def write_run_metrics(metrics: dict) -> None:
    def _insert() -> None:
        _client.table("run_metrics").insert(metrics).execute()
    await asyncio.to_thread(_insert)


async def set_bland_status(case_number: str, track: str, status: str, call_id: str | None = None) -> None:
    col_status = "bland_status" if track == "ec" else "ng_bland_status"
    col_call_id = "bland_call_id" if track == "ec" else "ng_bland_call_id"
    def _update() -> None:
        payload: dict = {col_status: status}
        if call_id:
            payload[col_call_id] = call_id
        _client.table("filings").update(payload).eq("case_number", case_number).execute()
    await asyncio.to_thread(_update)


async def get_pending_leads(track: str = "ec", limit: int = 200) -> list[dict]:
    col_status = "bland_status" if track == "ec" else "ng_bland_status"
    col_ghl = "ghl_contact_id" if track == "ec" else "ng_ghl_contact_id"
    def _query() -> list[dict]:
        result = (
            _client.table("filings")
            .select(
                "case_number,tenant_name,landlord_name,property_address,"
                "state,county,filing_date,court_date,phone,email,"
                f"property_type,dnc_status,dnc_source,{col_status},{col_ghl}"
            )
            .eq(col_status, "pending")
            .not_.is_(col_ghl, "null")
            .order("filing_date", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data
    return await asyncio.to_thread(_query)


_DASHBOARD_SELECT = (
    "case_number,tenant_name,landlord_name,property_address,"
    "state,county,filing_date,court_date,phone,email,"
    "property_type,estimated_rent,property_zip,lead_bucket,"
    "discard_reason,qualification_notes,dnc_status,dnc_source,language_hint,"
    "bland_status,ghl_contact_id"
)


def _filter_dashboard_query(query, view: str):
    if view == "commercial":
        return query.eq("lead_bucket", "commercial").or_(
            "language_hint.is.null,language_hint.neq.spanish_likely"
        )
    if view == "spanish_residential":
        return query.eq("lead_bucket", "residential_approved").eq("language_hint", "spanish_likely")
    if view == "spanish_commercial":
        return query.eq("lead_bucket", "commercial").eq("language_hint", "spanish_likely")
    if view == "held":
        return query.eq("lead_bucket", "held")
    if view == "discarded":
        return query.eq("lead_bucket", "discarded")
    return query.eq("lead_bucket", "residential_approved").or_(
        "language_hint.is.null,language_hint.neq.spanish_likely"
    )


async def get_dashboard_leads(view: str = "residential_approved", limit: int = 500) -> list[dict]:
    def _query() -> list[dict]:
        query = _client.table("filings").select(_DASHBOARD_SELECT)
        query = _filter_dashboard_query(query, view)

        result = (
            query
            .order("filing_date", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data
    return await asyncio.to_thread(_query)


def _dashboard_counts_from_rows(rows: list[dict]) -> dict:
    counts = {
        "residential_approved": 0,
        "commercial": 0,
        "held": 0,
        "discarded": 0,
        "spanish_residential": 0,
        "spanish_commercial": 0,
    }
    for row in rows:
        bucket = row.get("lead_bucket")
        spanish_likely = row.get("language_hint") == "spanish_likely"
        if bucket == "residential_approved":
            if spanish_likely:
                counts["spanish_residential"] += 1
            else:
                counts["residential_approved"] += 1
        elif bucket == "commercial":
            if spanish_likely:
                counts["spanish_commercial"] += 1
            else:
                counts["commercial"] += 1
        elif bucket == "held":
            counts["held"] += 1
        elif bucket == "discarded":
            counts["discarded"] += 1
        elif (
            bucket is None
            and row.get("bland_status") == "pending"
            and row.get("ghl_contact_id")
        ):
            counts["residential_approved"] += 1
    return counts


async def get_dashboard_counts() -> dict:
    def _query() -> dict:
        rows = (
            _client.table("filings")
            .select("lead_bucket,property_type,language_hint,bland_status,ghl_contact_id")
            .execute()
            .data
        )

        return _dashboard_counts_from_rows(rows)
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
