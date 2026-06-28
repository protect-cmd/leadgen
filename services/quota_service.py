"""Atomic per-business spend quota (PLAN.md Phase 5).

Every paid action (SearchBug enrichment, GHL push, Bland dial) reserves a quota
slot BEFORE acting. Reservation is atomic and idempotent (backed by the
quota_ledger table + quota_try_reserve function from migration 028), so:

  * a business can never exceed its daily cap for an action, even under
    concurrent stage runs;
  * retrying/replaying the same lead never double-counts;
  * a reserved-but-failed action can be rolled back to free the slot.

Caps are config-by-policy (env), so the operator sets them once and the pipeline
obeys hands-off. The cap is the standing pre-approval (see PLAN.md
"Spend-authority model"); only raising a cap is a manual gate.

Usage (in the two-stage guard):
    res = await quota_service.try_reserve(Business.VANTAGE, "searchbug", case_no)
    if not res.granted:
        # cap reached for the day -> hold the lead, do NOT spend
        return
    try:
        ... do the paid call ...
        await quota_service.commit(Business.VANTAGE, "searchbug", case_no)
    except Exception:
        await quota_service.rollback(Business.VANTAGE, "searchbug", case_no)
        raise
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone

from dotenv import load_dotenv
from supabase import Client, create_client

from pipeline.contract import Business

load_dotenv()

log = logging.getLogger(__name__)

_client: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)

_TABLE = "quota_ledger"

# Fallback when no per-(business, action) or per-action override is set.
_GLOBAL_DEFAULT_CAP = int(os.getenv("QUOTA_DEFAULT_CAP", "100"))


@dataclass(frozen=True)
class ReserveResult:
    granted: bool
    used: int
    remaining: int


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def cap_for(business: Business, action: str) -> int:
    """Resolve the daily cap for (business, action) from env, most specific first:
        QUOTA_CAP_<BUSINESS>_<ACTION>  e.g. QUOTA_CAP_VANTAGE_SEARCHBUG
        QUOTA_CAP_<ACTION>             e.g. QUOTA_CAP_SEARCHBUG
        QUOTA_DEFAULT_CAP              global fallback
    """
    b = business.value.upper()
    a = action.upper()
    for key in (f"QUOTA_CAP_{b}_{a}", f"QUOTA_CAP_{a}"):
        raw = os.getenv(key)
        if raw:
            try:
                return int(raw)
            except ValueError:
                log.warning("Invalid %s=%r; ignoring", key, raw)
    return _GLOBAL_DEFAULT_CAP


async def try_reserve(
    business: Business,
    action: str,
    lead_key: str,
    *,
    cap: int | None = None,
    day: str | None = None,
) -> ReserveResult:
    """Atomically reserve a quota slot. granted=False means the cap is reached
    for the day — the caller MUST NOT perform the paid action."""
    import asyncio

    eff_cap = cap if cap is not None else cap_for(business, action)
    eff_day = day or _today()

    def _do() -> ReserveResult:
        resp = _client.rpc(
            "quota_try_reserve",
            {
                "p_business": business.value,
                "p_action": action,
                "p_lead_key": lead_key,
                "p_day": eff_day,
                "p_cap": eff_cap,
            },
        ).execute()
        row = (resp.data or [{}])[0] if isinstance(resp.data, list) else (resp.data or {})
        return ReserveResult(
            granted=bool(row.get("granted")),
            used=int(row.get("used") or 0),
            remaining=int(row.get("remaining") or 0),
        )

    try:
        return await asyncio.to_thread(_do)
    except Exception as exc:
        # Fail CLOSED: if the quota backend is unreachable, deny the spend
        # rather than risk an uncapped burn.
        log.error("quota try_reserve failed (denying to be safe): %s", exc)
        return ReserveResult(granted=False, used=0, remaining=0)


async def _set_status(
    business: Business, action: str, lead_key: str, status: str, day: str | None
) -> None:
    import asyncio

    eff_day = day or _today()
    now = datetime.now(timezone.utc).isoformat()

    def _do() -> None:
        _client.table(_TABLE).update({"status": status, "updated_at": now}).eq(
            "business", business.value
        ).eq("action", action).eq("lead_key", lead_key).eq("day", eff_day).eq(
            "status", "reserved"
        ).execute()

    await asyncio.to_thread(_do)


async def commit(
    business: Business, action: str, lead_key: str, *, day: str | None = None
) -> None:
    """Mark a reservation committed (the paid action succeeded)."""
    await _set_status(business, action, lead_key, "committed", day)


async def rollback(
    business: Business, action: str, lead_key: str, *, day: str | None = None
) -> None:
    """Release a reservation (the paid action failed / was skipped) so the slot
    is freed and the lead can be retried later."""
    await _set_status(business, action, lead_key, "rolled_back", day)


async def remaining(
    business: Business, action: str, *, cap: int | None = None, day: str | None = None
) -> int:
    """Slots left today for (business, action). For dashboards/monitoring."""
    import asyncio

    eff_cap = cap if cap is not None else cap_for(business, action)
    eff_day = day or _today()

    def _do() -> int:
        resp = (
            _client.table(_TABLE)
            .select("id", count="exact")
            .eq("business", business.value)
            .eq("action", action)
            .eq("day", eff_day)
            .in_("status", ["reserved", "committed"])
            .execute()
        )
        used = resp.count or 0
        return max(eff_cap - used, 0)

    return await asyncio.to_thread(_do)
