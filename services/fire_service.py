"""Fire a lead: stage to GHL (if not already) + dial Bland. Used by the /lists
'Fire selected' action and re-usable from scripts.

A lead is "fired" when it has a GHL contact AND a Bland call dispatched. For an
already-staged lead this is just the Bland call; for an unstaged one it's both.
Per-lead, fault-isolated — one failure never blocks the rest.
"""
from __future__ import annotations

import logging
from datetime import date

from models.contact import EnrichedContact
from models.filing import Filing

log = logging.getLogger(__name__)

_FILING_COLS = ("case_number,tenant_name,property_address,landlord_name,filing_date,"
                "court_date,state,county,notice_type,source_url,estimated_rent,property_type")


def _d(v):
    return date.fromisoformat(v) if isinstance(v, str) and v else (v or date.today())


async def fire_case(sb, case_number: str) -> dict:
    """Stage (if needed) + dial one ng lead. Returns a result dict."""
    from services import bland_service, ghl_service
    from services.dedup_service import update_ghl_id, set_bland_status
    from pipeline import router, runner
    from pipeline.runner import _language_tags

    lc = (sb.table("lead_contacts").select("phone,language_hint,ghl_contact_id,bland_call_id")
          .eq("case_number", case_number).eq("track", "ng")
          .not_.is_("phone", "null").limit(1).execute().data or [])
    if not lc:
        return {"case_number": case_number, "status": "no_phone"}
    lc = lc[0]
    if lc.get("bland_call_id"):
        return {"case_number": case_number, "status": "already_dialed"}

    # DNC compliance gate — scrub at dial-time (TCPA: scrub close to call-time).
    # Independent of the stored dnc_status, so we never dial a DNC number even if
    # the To-Fire list was built before scrubbing.
    from services import dnc_service
    v = dnc_service.verdict(lc["phone"])
    try:
        sb.table("lead_contacts").update({"dnc_status": v}).eq(
            "case_number", case_number).eq("track", "ng").execute()
    except Exception:
        pass  # dnc_status column may not be live yet — gate still enforced below
    if v == "dnc":
        return {"case_number": case_number, "status": "dnc_skip"}

    f = (sb.table("filings").select(_FILING_COLS)
         .eq("case_number", case_number).limit(1).execute().data or [])
    if not f:
        return {"case_number": case_number, "status": "no_filing"}
    f = f[0]

    # TCPA calling-hours gate (lead-local). Skip without staging GHL so the lead
    # stays in the To-Fire queue and is retried when the window opens.
    from services.call_window import in_call_window
    if not in_call_window(f.get("state")):
        return {"case_number": case_number, "status": "outside_window"}

    filing = Filing(
        case_number=f["case_number"], tenant_name=f.get("tenant_name") or "",
        property_address=f.get("property_address") or "", landlord_name=f.get("landlord_name") or "",
        filing_date=_d(f.get("filing_date")), court_date=_d(f["court_date"]) if f.get("court_date") else None,
        state=f["state"], county=f["county"], notice_type=f.get("notice_type") or "Eviction",
        source_url=f.get("source_url") or "", claim_amount=f.get("estimated_rent"),
        property_type_hint=f.get("property_type"))
    ec = EnrichedContact(filing=filing, track="ng", phone=lc["phone"],
                         language_hint=lc.get("language_hint"),
                         property_type=f.get("property_type") or "residential",
                         searchbug_status="phone_found")

    # 1) stage to GHL if not already
    ghl_id = lc.get("ghl_contact_id")
    if not ghl_id:
        outcome = router.route_ng(ec)
        stage_id = (runner.GHL_NG_COMMERCIAL_STAGE_ID if outcome.pipeline == "commercial"
                    else runner.GHL_NG_RESIDENTIAL_STAGE_ID)
        tags = [outcome.tag] + _language_tags(ec)
        try:
            ghl_id = await ghl_service.create_contact(ec, tags, stage_id)
            await update_ghl_id(case_number, ghl_id, "ng")
        except Exception as e:
            return {"case_number": case_number, "status": "ghl_failed", "error": repr(e)[:120]}

    # 2) dial Bland
    try:
        call_id = await bland_service.trigger_voicemail(ec)
        await set_bland_status(case_number, "ng", "triggered", call_id=call_id)
        return {"case_number": case_number, "status": "fired", "ghl_id": ghl_id, "call_id": call_id}
    except Exception as e:
        await set_bland_status(case_number, "ng", "pending")
        rl = "429" in repr(e)
        return {"case_number": case_number, "status": "rate_limited" if rl else "bland_failed",
                "ghl_id": ghl_id, "error": repr(e)[:120]}


async def fire_cases(sb, case_numbers: list[str], cap: int = 25) -> dict:
    """Fire up to `cap` leads. Returns {results: [...], summary: {...}}."""
    from collections import Counter
    results = []
    for cn in case_numbers[:cap]:
        results.append(await fire_case(sb, cn))
    summary = dict(Counter(r["status"] for r in results))
    return {"results": results, "summary": summary, "capped": len(case_numbers) > cap}


async def ists_fire_case(sb, case_number: str) -> dict:
    """ISTS fire: push to GHL (ists_ghl) if needed + dial (ists_bland, DNC-gated)."""
    from datetime import datetime, timezone
    from services import ists_ghl, ists_bland

    rec = (sb.table("ists_judgments").select("*")
           .eq("case_number", case_number).limit(1).execute().data or [])
    if not rec:
        return {"case_number": case_number, "status": "no_record"}
    rec = rec[0]
    if not rec.get("phone"):
        return {"case_number": case_number, "status": "no_phone"}
    if rec.get("bland_call_id"):
        return {"case_number": case_number, "status": "already_dialed"}
    now = datetime.now(timezone.utc).isoformat()

    ghl_id = rec.get("ghl_contact_id")
    if not ghl_id:
        try:
            ghl_id = await ists_ghl.push_contact(rec)
            if ghl_id:
                sb.table("ists_judgments").update(
                    {"ghl_contact_id": ghl_id, "ghl_pushed_at": now}
                ).eq("case_number", case_number).execute()
                rec["ghl_contact_id"] = ghl_id
        except Exception as e:
            return {"case_number": case_number, "status": "ghl_failed", "error": repr(e)[:120]}

    try:
        call_id = await ists_bland.trigger_call(rec)
    except Exception as e:
        return {"case_number": case_number, "status": "bland_failed", "error": repr(e)[:120]}
    if call_id == "dnc_skip":
        return {"case_number": case_number, "status": "dnc_skip"}
    if call_id in (None, "outside_window"):
        return {"case_number": case_number, "status": call_id or "failed"}
    sb.table("ists_judgments").update(
        {"bland_call_id": call_id, "bland_triggered_at": now}
    ).eq("case_number", case_number).execute()
    return {"case_number": case_number, "status": "fired", "ghl_id": ghl_id, "call_id": call_id}


async def fire_cases_track(sb, case_numbers: list[str], track: str = "vantage", cap: int = 25) -> dict:
    """Dispatch to the right fire path by track ('vantage' | 'ists')."""
    from collections import Counter
    fire = ists_fire_case if track == "ists" else fire_case
    results = [await fire(sb, cn) for cn in case_numbers[:cap]]
    return {"results": results, "summary": dict(Counter(r["status"] for r in results)),
            "capped": len(case_numbers) > cap}
