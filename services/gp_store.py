"""Isolated persistence for Garnish Proof. Writes ONLY garnishment_orders."""
from __future__ import annotations
import asyncio
import os
from dotenv import load_dotenv
from supabase import create_client, Client
from models.garnishment import GarnishmentRecord

load_dotenv()

_client: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)

_TABLE = "garnishment_orders"


async def upsert_order(record: GarnishmentRecord) -> None:
    def _do() -> None:
        _client.table(_TABLE).upsert(
            record.to_row(), on_conflict="case_number"
        ).execute()
    await asyncio.to_thread(_do)


async def existing_case_numbers(case_numbers: list[str]) -> set[str]:
    """Return case numbers already stored, for idempotent re-runs."""
    if not case_numbers:
        return set()
    def _q() -> set[str]:
        found: set[str] = set()
        for i in range(0, len(case_numbers), 200):
            chunk = case_numbers[i:i + 200]
            data = (_client.table(_TABLE).select("case_number")
                    .in_("case_number", chunk).execute().data or [])
            found.update(d["case_number"] for d in data)
        return found
    return await asyncio.to_thread(_q)
