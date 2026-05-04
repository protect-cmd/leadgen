from __future__ import annotations
import asyncio
import os
from dotenv import load_dotenv
from supabase import create_client, Client
from models.filing import Filing
from models.contact import EnrichedContact, RoutingOutcome

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


async def update_enrichment(contact: EnrichedContact) -> None:
    def _update() -> None:
        _client.table("filings").update({
            "phone": contact.phone,
            "email": contact.email,
            "secondary_address": contact.secondary_address,
            "estimated_rent": contact.estimated_rent,
            "property_type": contact.property_type,
        }).eq("case_number", contact.filing.case_number).execute()
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
                f"property_type,{col_status},{col_ghl}"
            )
            .eq(col_status, "pending")
            .not_.is_(col_ghl, "null")
            .order("filing_date", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data
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
