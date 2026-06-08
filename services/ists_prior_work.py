"""Read-only cross-reference of ISTS judgment records against existing Vantage
lead_contacts (track='ng'). SELECT-only — never writes prod tables."""
from __future__ import annotations
import asyncio
import os
from dotenv import load_dotenv
from supabase import create_client, Client
from models.judgment import JudgmentRecord

load_dotenv()

_client: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)


async def annotate_prior_work(records: list[JudgmentRecord]) -> list[JudgmentRecord]:
    """Annotate records with prior_phone / prior_bland_status from lead_contacts."""
    case_numbers = [r.case_number for r in records]
    if not case_numbers:
        return records

    def _q() -> dict[str, dict]:
        found: dict[str, dict] = {}
        for i in range(0, len(case_numbers), 200):
            chunk = case_numbers[i:i + 200]
            rows = (_client.table("lead_contacts")
                    .select("case_number,phone,bland_status")
                    .in_("case_number", chunk).eq("track", "ng")
                    .execute().data or [])
            for row in rows:
                found[row["case_number"]] = row
        return found

    prior = await asyncio.to_thread(_q)
    for r in records:
        hit = prior.get(r.case_number)
        if hit:
            r.prior_phone = bool(hit.get("phone"))
            r.prior_bland_status = hit.get("bland_status")
    return records
