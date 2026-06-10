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

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from dashboard.auth import require_search, require_queue, require_any
from services import bland_service, daily_scheduler, notification_service
from services.dedup_service import (
    get_dashboard_counts,
    get_dashboard_leads,
    get_pending_leads,
    get_recent_metrics,
    set_bland_status,
    search_leads,
    add_lead_note,
    list_lead_notes,
    mark_lead_called,
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
_SEARCH_HTML = Path(__file__).parent / "search.html"
_LISTS_HTML = Path(__file__).parent / "lists.html"
_DNC_DIR = os.getenv("DNC_DIR", r"C:\Users\Zeann\Downloads\DNC Scrub")


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
        language_hint="spanish_likely" if is_spanish else None,
    )


@app.get("/", response_class=FileResponse, dependencies=[Depends(require_search)])
async def dashboard_search():
    """Search-first landing page (Spec 4)."""
    return FileResponse(_SEARCH_HTML)


@app.get("/queue", response_class=FileResponse, dependencies=[Depends(require_queue)])
async def dashboard_queue():
    """Legacy multi-tab queue UI (moved from / in Spec 4)."""
    return FileResponse(_HTML)


@app.get("/lists", response_class=FileResponse, dependencies=[Depends(require_queue)])
async def dashboard_lists():
    """Scored work lists: To Enrich / To Fire, with select-first-N + CSV export."""
    return FileResponse(_LISTS_HTML)


@app.get("/api/queue/{which}", dependencies=[Depends(require_queue)])
async def api_queue(which: str, limit: int = 0):
    """which = 'to-enrich' (good_leads_now, needs SearchBug) or
    'to-fire' (enriched + actionable + not-yet-dialed, needs Bland)."""
    from services.dedup_service import _client as sb
    from pipeline.queue_builder import build_to_enrich, build_to_fire
    if which == "to-enrich":
        rows = await asyncio.to_thread(build_to_enrich, sb, _DNC_DIR)
    elif which == "to-fire":
        rows = await asyncio.to_thread(build_to_fire, sb, _DNC_DIR)
    else:
        raise HTTPException(404, "unknown list")
    return JSONResponse(rows[:limit] if limit else rows)


@app.get("/api/search", dependencies=[Depends(require_search)])
async def api_search(q: str = "", limit: int = 20):
    if not q or len(q.strip()) < 2:
        return JSONResponse([])
    rows = await search_leads(q=q, limit=limit)
    return JSONResponse(rows)


@app.post("/api/leads/{case_number}/note", dependencies=[Depends(require_search)])
async def api_add_note(case_number: str, payload: dict, track: str = "ng"):
    text = (payload or {}).get("text", "")
    try:
        row = await add_lead_note(case_number=case_number, track=track, text=text)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return JSONResponse(row)


@app.get("/api/leads/{case_number}/notes", dependencies=[Depends(require_any)])
async def api_list_notes(case_number: str, track: str = "ng"):
    rows = await list_lead_notes(case_number=case_number, track=track)
    return JSONResponse(rows)


@app.post("/api/leads/{case_number}/mark-called", dependencies=[Depends(require_search)])
async def api_mark_called(case_number: str, track: str = "ng"):
    ts = await mark_lead_called(case_number=case_number, track=track)
    return JSONResponse({"status": "ok", "last_called_at": ts})


@app.get("/api/leads", dependencies=[Depends(require_queue)])
async def leads(track: str = "ec", view: str = "residential_approved"):
    if view:
        rows = await get_dashboard_leads(view=view)
        return JSONResponse(rows)
    rows = await get_pending_leads(track=track)
    return JSONResponse(rows)


@app.get("/api/lead-counts", dependencies=[Depends(require_queue)])
async def lead_counts():
    return JSONResponse(await get_dashboard_counts())


@app.get("/api/metrics", dependencies=[Depends(require_queue)])
async def metrics():
    rows = await get_recent_metrics(limit=10)
    return JSONResponse(rows)


@app.post("/api/bland-test-calls/{track}", dependencies=[Depends(require_queue)])
async def bland_test_call(track: str):
    if not _bland_test_calls_enabled():
        raise HTTPException(403, "Bland QA test calls are disabled")
    contact = _build_bland_test_contact(track)
    try:
        call_id = await bland_service.trigger_voicemail(contact)
    except Exception as e:
        raise HTTPException(500, str(e))
    return {"status": "triggered", "track": track, "call_id": call_id}


@app.post("/api/leads/{case_number}/approve", dependencies=[Depends(require_queue)])
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
    )

    try:
        call_id = await bland_service.trigger_voicemail(contact)
    except Exception as e:
        raise HTTPException(500, str(e))

    await set_bland_status(case_number, track, "triggered", call_id=call_id)
    return {"status": "triggered", "call_id": call_id}


@app.post("/api/leads/{case_number}/skip", dependencies=[Depends(require_any)])
async def skip(case_number: str, track: str = "ec"):
    await set_bland_status(case_number, track, "skipped")
    return {"status": "skipped"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("dashboard.main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=False)
