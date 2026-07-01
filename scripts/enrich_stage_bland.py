"""Enrich N leads from chosen counties -> DNC scrub -> stage CALLABLE to GHL +
DIAL Bland. On-DNC are skipped; no-list (no DNC file) are HELD (persisted, not
dialed). Calls Bland directly so dialing does NOT depend on AUTO_BLAND_CALLS_ENABLED.

Selection: residential_approved + filing_date >= today-DAYS + (court null or
future) + no ng phone yet + clean tenant name, round-robin across --counties
(freshest filing first), excludes prior-batch CSVs in outputs/.

Usage:
    python scripts/enrich_stage_bland.py --counties Harris,Franklin --max-enrich 100 --days 14 --dry-run
    python scripts/enrich_stage_bland.py --counties Harris,Franklin --max-enrich 100 --days 14 --yes-live
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import glob
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv

DNC_DIR = r"C:\Users\Zeann\Downloads\DNC Scrub"
_dnc_cache: dict[str, set | None] = {}
ENT = (" LLC", " INC", "D/B/A", "DBA", " LP", " L.P", "REIT", " TRUST", "PROPERTIES",
       "APARTMENTS", "MANAGEMENT", "HOLDINGS", "PARTNERS", "ASSOCIATES", " CORP")


def _dnc_set(area: str):
    if area in _dnc_cache:
        return _dnc_cache[area]
    m = glob.glob(os.path.join(DNC_DIR, f"*_{area}_*.txt"))
    if not m:
        _dnc_cache[area] = None
        return None
    s = set()
    with open(m[0], encoding="utf-8", errors="ignore") as f:
        for line in f:
            p = line.strip().split(",")
            if len(p) == 2:
                s.add(p[1])
    _dnc_cache[area] = s
    return s


def on_dnc(phone):
    d = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(d) == 11 and d[0] == "1":
        d = d[1:]
    if len(d) != 10:
        return None
    s = _dnc_set(d[:3])
    return None if s is None else (d[3:] in s)


async def main_async(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--counties", default="Harris,Franklin", help="comma-sep county names to include")
    ap.add_argument("--vendor", choices=["searchbug", "enformion"], default="searchbug",
                    help="enrichment vendor (enformion = no credit-cap wall)")
    ap.add_argument("--stage", type=int, default=0, help="stop after N CALLABLE staged (0 = no target)")
    ap.add_argument("--found", type=int, default=0, help="stop after N PHONES found (0 = no target)")
    ap.add_argument("--max-enrich", type=int, default=100, help="SearchBug query budget / safety cap")
    ap.add_argument("--days", type=int, default=14, help="filing freshness window")
    ap.add_argument("--from-queue", action="store_true",
                    help="source from good_leads_now in priority+score order (ignores --counties/--days)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes-live", action="store_true")
    a = ap.parse_args(argv)
    if not a.dry_run and not a.yes_live:
        ap.error("Pass --dry-run or --yes-live")

    load_dotenv()
    from supabase import create_client
    from services import batchdata_service, enformion_service, dedup_service, bland_service, ghl_service, dnc_service
    enrich_vendor = enformion_service if a.vendor == "enformion" else batchdata_service
    from services.dedup_service import update_ghl_id, set_bland_status
    from pipeline import router, runner
    from pipeline.runner import _language_tags
    from pipeline.gates import gate_name
    from models.contact import EnrichedContact
    from models.filing import Filing

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    want = {c.strip().lower() for c in a.counties.split(",") if c.strip()}
    today = date.today().isoformat()
    fresh = (date.today() - timedelta(days=a.days)).isoformat()

    def is_entity(n):
        u = (n or "").upper()
        return any(t in u for t in ENT)

    # prior-batch exclusions
    exclude = set()
    for p in (glob.glob("outputs/enrich_*.csv") + glob.glob("outputs/staged_callable_*.csv")
              + glob.glob("outputs/attempted_*.csv")):
        try:
            with open(p, newline="", encoding="utf-8") as f:
                exclude.update(r["case_number"].strip() for r in csv.DictReader(f)
                               if r.get("case_number", "").strip())
        except Exception:
            pass

    cols = ("case_number,tenant_name,property_address,state,county,filing_date,court_date,"
            "landlord_name,notice_type,estimated_rent,property_type,source_url")
    rows, off = [], 0
    while True:
        b = (sb.table("filings").select(cols).eq("lead_bucket", "residential_approved")
             .gte("filing_date", fresh).order("filing_date", desc=True)
             .range(off, off + 999).execute().data)
        rows += b
        if len(b) < 1000:
            break
        off += 1000
    pool = [r for r in rows
            if (r.get("county") or "").lower() in want
            and (not r.get("court_date") or r["court_date"] >= today)
            and r["case_number"] not in exclude]

    cns = [r["case_number"] for r in pool]
    phoned = set()
    for i in range(0, len(cns), 200):
        d = (sb.table("lead_contacts").select("case_number").in_("case_number", cns[i:i + 200])
             .eq("track", "ng").not_.is_("phone", "null").execute().data or [])
        phoned.update(x["case_number"] for x in d)
    pool = [r for r in pool if r["case_number"] not in phoned
            and gate_name(r.get("tenant_name") or "") and not is_entity(r["tenant_name"])]

    # round-robin across counties, freshest filing first
    buckets = defaultdict(list)
    for r in sorted(pool, key=lambda r: r["filing_date"], reverse=True):
        buckets[(r["state"], r["county"])].append(r)
    order = sorted(buckets, key=lambda k: -len(buckets[k]))
    idx = {k: 0 for k in buckets}
    queue = []
    while len(queue) < a.max_enrich and any(idx[k] < len(buckets[k]) for k in order):
        for k in order:
            if idx[k] < len(buckets[k]):
                queue.append(buckets[k][idx[k]]); idx[k] += 1
                if len(queue) >= a.max_enrich:
                    break

    if a.from_queue:
        from pipeline.queue_builder import build_to_enrich
        dnc_dir = os.getenv("DNC_DIR", r"C:\Users\Zeann\Downloads\DNC Scrub")
        queue = build_to_enrich(sb, dnc_dir)[:a.max_enrich]
        print(f"sourced from good_leads_now (priority+score order): {len(queue)}")
    else:
        print(f"enrichable pool ({'/'.join(sorted(want))}, filing>= {fresh}): {len(pool)}")
    print(f"  county mix in queue: {dict(Counter((r['state'],r['county']) for r in queue).most_common())}")
    print(f"  enrich budget: {a.max_enrich}   mode: {'DRY-RUN' if a.dry_run else 'LIVE'}\n", flush=True)

    def to_filing(r):
        return Filing(
            case_number=r["case_number"], tenant_name=r["tenant_name"] or "",
            property_address=r["property_address"] or "", landlord_name=r.get("landlord_name") or "",
            filing_date=date.fromisoformat(r["filing_date"]) if r.get("filing_date") else date.today(),
            court_date=date.fromisoformat(r["court_date"]) if r.get("court_date") else None,
            state=r["state"], county=r["county"], notice_type=r.get("notice_type") or "Eviction",
            source_url=r.get("source_url") or "", claim_amount=r.get("estimated_rent") or None,
            property_type_hint=r.get("property_type") or None)

    if a.dry_run:
        for n, r in enumerate(queue, 1):
            print(f"  [{n:3}/{len(queue)}] {(r['tenant_name'] or '')[:24]:24} {r['county']:8} "
                  f"{r['filing_date']} court={r.get('court_date') or '-'}")
        print(f"\nDRY-RUN: would enrich {len(queue)} leads. No SearchBug/GHL/Bland calls made.")
        return 0

    enriched = found = staged = dialed = dnc_hit = held = 0
    consec_fail = 0
    staged_cases = []
    attempted_cases = []   # only REAL outcomes (exclude (none)/account_error depletion noise)
    for r in queue:
        if enriched >= a.max_enrich or (a.stage and staged >= a.stage) or (a.found and found >= a.found):
            break
        try:
            ng = await enrich_vendor.enrich_tenant(to_filing(r), lookup_property_if_missing=False)
            phone, status = ng.phone, (ng.searchbug_status or "(none)")
            if phone:
                await dedup_service.update_enrichment(ng)
        except Exception as e:
            phone, status = None, f"error:{e!r}"[:30]
            ng = None
        enriched += 1
        if status not in ("(none)", "account_error", "enformion_error"):
            attempted_cases.append(r["case_number"])

        if not phone:
            consec_fail += 1
            print(f"  #{enriched:3} {r['county']:8} no-phone ({status})", flush=True)
            if consec_fail >= 8 and status in ("account_error", "(none)", "enformion_error"):
                print(f"\n!! {consec_fail} consecutive no-phone ({status}) -> vendor unavailable. STOPPING.", flush=True)
                break
            continue
        consec_fail = 0
        found += 1

        # DNCScrub at enrich-time (national, all area codes) — verdict stored on the
        # number so To-Fire shows only scrubbed-callable. No more 'held' bucket.
        verdict = dnc_service.verdict(phone)
        try:  # dnc_status column may not be live yet — verdict still gates below
            sb.table("lead_contacts").update(
                {"dnc_status": verdict, "dnc_checked_at": datetime.now(timezone.utc).isoformat()}
            ).eq("case_number", r["case_number"]).eq("track", "ng").execute()
        except Exception:
            pass
        if verdict == "dnc":
            dnc_hit += 1
            print(f"  #{enriched:3} {r['county']:8} {phone:11} ON-DNC (skip)", flush=True)
            continue

        # name_mismatch / ambiguous = wrong-party risk -> GHL review stage only,
        # NEVER auto-dial (TCPA). Routed before the dial block below.
        if ng.searchbug_status in ("name_mismatch", "ambiguous"):
            ec = EnrichedContact(filing=to_filing(r), track="ng", phone=phone, email=ng.email,
                                 secondary_address=ng.secondary_address, estimated_rent=ng.estimated_rent,
                                 property_type=ng.property_type, language_hint=ng.language_hint,
                                 searchbug_status=ng.searchbug_status)
            if runner.GHL_NG_REVIEW_STAGE_ID:
                try:
                    ghl_id = await ghl_service.create_contact(
                        ec, ["Review-NameMismatch"] + _language_tags(ec),
                        runner.GHL_NG_REVIEW_STAGE_ID)
                    await update_ghl_id(r["case_number"], ghl_id, "ng")
                    print(f"  #{enriched:3} {r['county']:8} {phone:11} {ng.searchbug_status} -> REVIEW GHL={ghl_id} (no Bland)", flush=True)
                except Exception as e:
                    print(f"  #{enriched:3} {r['case_number']} REVIEW push FAILED: {e!r}", flush=True)
            else:
                print(f"  #{enriched:3} {r['county']:8} {phone:11} {ng.searchbug_status} (no review stage set; held, no Bland)", flush=True)
            continue

        # callable -> stage GHL + dial Bland
        ec = EnrichedContact(filing=to_filing(r), track="ng", phone=phone, email=ng.email,
                             secondary_address=ng.secondary_address, estimated_rent=ng.estimated_rent,
                             property_type=ng.property_type, language_hint=ng.language_hint,
                             searchbug_status="phone_found")
        outcome = router.route_ng(ec)
        stage_id = (runner.GHL_NG_COMMERCIAL_STAGE_ID if outcome.pipeline == "commercial"
                    else runner.GHL_NG_RESIDENTIAL_STAGE_ID)
        tags = [outcome.tag] + _language_tags(ec)
        es = "ES" if ec.language_hint == "spanish_likely" else "EN"
        try:
            ghl_id = await ghl_service.create_contact(ec, tags, stage_id)
            await update_ghl_id(r["case_number"], ghl_id, "ng")
            staged += 1
            staged_cases.append(r["case_number"])
        except Exception as e:
            print(f"  #{enriched:3} {r['case_number']} GHL FAILED: {e!r}", flush=True)
            continue
        try:
            call_id = await bland_service.trigger_voicemail(ec)
            await set_bland_status(r["case_number"], "ng", "triggered", call_id=call_id)
            dialed += 1
            print(f"  #{enriched:3} {r['county']:8} {phone:11} {es} CALLABLE -> GHL={ghl_id} BLAND={call_id}", flush=True)
        except Exception as e:
            await set_bland_status(r["case_number"], "ng", "pending")
            print(f"  #{enriched:3} {r['county']:8} {phone:11} STAGED but BLAND FAILED: {e!r}", flush=True)
        await asyncio.sleep(0.3)

    print(f"\n=== Run summary ===")
    print(f"  enriched (SearchBug calls): {enriched}")
    print(f"  phones found:               {found}")
    print(f"  CALLABLE staged to GHL:     {staged}")
    print(f"  dialed via Bland:           {dialed}")
    print(f"  ON-DNC (scrubbed, skipped): {dnc_hit}")
    print(f"  no phone:                   {enriched - found}")
    import time
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    if staged_cases:
        Path(f"outputs/staged_callable_{stamp}.csv").write_text(
            "case_number\n" + "\n".join(staged_cases) + "\n", encoding="utf-8")
    if attempted_cases:
        Path(f"outputs/attempted_{stamp}.csv").write_text(
            "case_number\n" + "\n".join(attempted_cases) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
