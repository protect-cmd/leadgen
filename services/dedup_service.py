from __future__ import annotations
import asyncio
import os
from dotenv import load_dotenv
from supabase import create_client, Client
from models.filing import Filing
from models.contact import RoutingOutcome

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


async def update_routing(case_number: str, outcome: RoutingOutcome) -> None:
    def _update() -> None:
        _client.table("filings").update({
            "routed": True,
            "routing_outcome": outcome.action,
        }).eq("case_number", case_number).execute()
    await asyncio.to_thread(_update)


async def update_ghl_id(case_number: str, ghl_contact_id: str) -> None:
    def _update() -> None:
        _client.table("filings").update({
            "ghl_contact_id": ghl_contact_id,
        }).eq("case_number", case_number).execute()
    await asyncio.to_thread(_update)


async def mark_bland_triggered(case_number: str) -> None:
    def _update() -> None:
        _client.table("filings").update({
            "bland_triggered": True,
        }).eq("case_number", case_number).execute()
    await asyncio.to_thread(_update)
