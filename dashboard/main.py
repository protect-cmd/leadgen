from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from services import bland_service, daily_scheduler, dnc_service
from services.dedup_service import (
    clear_dnc_status,
    get_dashboard_counts,
    get_dashboard_leads,
    get_pending_leads,
    get_recent_metrics,
    set_bland_status,
)
from models.filing import Filing
from models.contact import EnrichedContact

_scheduler_task: asyncio.Task | None = None
_BLAND_TEST_RECIPIENTS = {
    "ec": "+18883224034",
    "ng": "+18882141711",
    "ng_spanish": "+18882141711",
}


async def start_daily_scheduler() -> None:
    global _scheduler_task
    if daily_scheduler.is_enabled() and _scheduler_task is None:
        _scheduler_task = asyncio.create_task(daily_scheduler.run_forever())


async def stop_daily_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task is not None:
        _scheduler_task.cancel()
        _scheduler_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    await start_daily_scheduler()
    try:
        yield
    finally:
        await stop_daily_scheduler()


app = FastAPI(title="Grant Ellis Group Lead Queue", lifespan=lifespan)

_HTML = Path(__file__).parent / "index.html"


class DncClearRequest(BaseModel):
    source: str = "manual_review"
    notes: str | None = None


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _bland_test_calls_enabled() -> bool:
    return _truthy(os.getenv("BLAND_ENABLED")) and _truthy(os.getenv("BLAND_TEST_CALLS_ENABLED"))


def _build_bland_test_contact(track: str) -> EnrichedContact:
    if track not in _BLAND_TEST_RECIPIENTS:
        raise HTTPException(404, "Unknown Bland QA track")

    is_ec = track == "ec"
    is_spanish = track == "ng_spanish"
    filing = Filing(
        case_number=f"QA-{track.upper()}-001",
        tenant_name="Maria Garcia" if is_spanish else "QA Tenant",
        landlord_name="QA Landlord",
        property_address="123 Main St, Houston, TX 77002",
        filing_date=date.today(),
        state="TX",
        county="Harris",
        notice_type="Eviction",
        source_url="https://example.test",
    )
    return EnrichedContact(
        filing=filing,
        track="ec" if is_ec else "ng",
        phone=_BLAND_TEST_RECIPIENTS[track],
        property_type="residential",
        dnc_status="clear",
        dnc_source="internal_qa",
        language_hint="spanish_likely" if is_spanish else None,
    )


@app.get("/", response_class=FileResponse)
async def dashboard():
    return FileResponse(_HTML)


@app.get("/api/leads")
async def leads(track: str = "ec", view: str = "residential_approved"):
    if view:
        rows = await get_dashboard_leads(view=view)
        return JSONResponse(rows)
    rows = await get_pending_leads(track=track)
    return JSONResponse(rows)


@app.get("/api/lead-counts")
async def lead_counts():
    return JSONResponse(await get_dashboard_counts())


@app.get("/api/metrics")
async def metrics():
    rows = await get_recent_metrics(limit=10)
    return JSONResponse(rows)


@app.post("/api/bland-test-calls/{track}")
async def bland_test_call(track: str):
    if not _bland_test_calls_enabled():
        raise HTTPException(403, "Bland QA test calls are disabled")
    contact = _build_bland_test_contact(track)
    try:
        call_id = await bland_service.trigger_voicemail(contact)
    except Exception as e:
        raise HTTPException(500, str(e))
    return {"status": "triggered", "track": track, "call_id": call_id}


@app.post("/api/leads/{case_number}/approve")
async def approve(case_number: str, track: str = "ec"):
    rows = await get_pending_leads(track=track, limit=500)
    row = next((r for r in rows if r["case_number"] == case_number), None)
    if not row:
        raise HTTPException(404, "Lead not found or already actioned")

    phone = row.get("phone")
    if not phone:
        raise HTTPException(400, "No phone number — cannot trigger Bland")

    # Reconstruct enough of the contact for bland_service
    from datetime import date
    filing = Filing(
        case_number=row["case_number"],
        tenant_name=row["tenant_name"],
        property_address=row["property_address"],
        landlord_name=row["landlord_name"],
        filing_date=date.fromisoformat(row["filing_date"]),
        court_date=date.fromisoformat(row["court_date"]) if row.get("court_date") else None,
        state=row["state"],
        county=row["county"],
        notice_type="Detainer Warrant",
        source_url="",
    )
    contact = EnrichedContact(
        filing=filing,
        track=track,
        phone=phone,
        email=row.get("email"),
        property_type=row.get("property_type"),
        dnc_status=row.get("dnc_status", "unknown"),
        dnc_source=row.get("dnc_source"),
    )

    dnc_decision = dnc_service.can_call(contact)
    if not dnc_decision.allowed:
        status = "blocked_dnc" if dnc_decision.status == "blocked" else "pending_dnc_review"
        await set_bland_status(case_number, track, status)
        raise HTTPException(400, f"DNC gate blocked Bland: {dnc_decision.reason}")

    try:
        call_id = await bland_service.trigger_voicemail(contact)
    except Exception as e:
        raise HTTPException(500, str(e))

    await set_bland_status(case_number, track, "triggered", call_id=call_id)
    return {"status": "triggered", "call_id": call_id}


@app.post("/api/leads/{case_number}/skip")
async def skip(case_number: str, track: str = "ec"):
    await set_bland_status(case_number, track, "skipped")
    return {"status": "skipped"}


@app.post("/api/leads/{case_number}/dnc-clear")
async def clear_dnc(case_number: str, request: DncClearRequest, track: str = "ec"):
    await clear_dnc_status(
        case_number,
        track=track,
        source=request.source,
        notes=request.notes,
    )
    return {"status": "clear", "source": request.source}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("dashboard.main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=False)
