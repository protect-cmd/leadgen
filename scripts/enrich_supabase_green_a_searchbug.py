from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from supabase import create_client

from services.name_utils import parse_name
from services.searchbug_service import search_tenant_detailed


def split_address(addr: str) -> dict[str, str]:
    parts = [p.strip() for p in (addr or "").split(",")]
    city = parts[-2] if len(parts) >= 3 else ""
    state = ""
    zipcode = ""
    if len(parts) >= 2:
        tail = parts[-1].split()
        if tail:
            state = tail[0]
        if len(tail) >= 2:
            zipcode = tail[1]
    return {"city": city, "state": state, "zipcode": zipcode}


def normalize(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def street_core(addr: str | None) -> str:
    return normalize((addr or "").split(",")[0])


def match_class(filing_addr: str, returned_addr: str | None) -> str:
    if not returned_addr:
        return "no_address"
    if normalize(filing_addr) == normalize(returned_addr):
        return "exact"
    if street_core(filing_addr) and street_core(filing_addr) == street_core(returned_addr):
        return "same_street"
    if street_core(filing_addr)[:8] and street_core(filing_addr)[:8] in street_core(returned_addr):
        return "near_street"
    return "different"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else ["case_number"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def already_processed(case_number: str, previous_rows: list[dict[str, str]]) -> bool:
    return any(
        row.get("case_number") == case_number
        and row.get("status") not in {"account_error", "api_error", "http_error"}
        for row in previous_rows
    )


def persist_hit(client, result: dict[str, Any]) -> bool:
    phone = (result.get("phone") or "").strip()
    if not phone:
        return False
    now = datetime.now(timezone.utc).isoformat()
    client.table("lead_contacts").upsert(
        {
            "case_number": result["case_number"],
            "track": "ng",
            "contact_name": result["tenant_name"],
            "phone": phone,
            "secondary_address": result.get("returned_address") or None,
            "dnc_status": "unknown",
            "dnc_source": "searchbug",
            "dnc_checked_at": now,
            "enrichment_source": "searchbug",
            "updated_at": now,
        },
        on_conflict="case_number,track",
    ).execute()
    client.table("filings").update(
        {
            "ng_dnc_status": "unknown",
            "ng_dnc_source": "searchbug",
            "ng_dnc_checked_at": now,
        }
    ).eq("case_number", result["case_number"]).execute()
    return True


async def enrich_row(row: dict[str, str], index: int) -> dict[str, Any]:
    first, last = parse_name(row["tenant_name"])
    addr = split_address(row["property_address"])
    base: dict[str, Any] = {
        "index": index,
        "case_number": row["case_number"],
        "tenant_name": row["tenant_name"],
        "property_address": row["property_address"],
        "county": row["county"],
        "state": row["state"],
        "filing_date": row["filing_date"],
        "court_date": row["court_date"],
        "query_first_name": first,
        "query_last_name": last,
        "query_city": addr["city"],
        "query_state": addr["state"],
        "query_zip": addr["zipcode"],
    }
    if not first or not last:
        return {
            **base,
            "status": "invalid_name",
            "phone": "",
            "returned_address": "",
            "address_match": "not_run",
            "rows": 0,
            "error_code": "",
            "error": "",
            "persisted": False,
        }

    search = await search_tenant_detailed(first, last, addr["city"], addr["state"], addr["zipcode"])
    return {
        **base,
        "status": search.status,
        "phone": search.phone or "",
        "returned_address": search.resolved_address or "",
        "address_match": match_class(row["property_address"], search.resolved_address),
        "rows": search.rows,
        "error_code": search.error_code or "",
        "error": search.error or "",
        "persisted": False,
    }


async def main_async(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run SearchBug enrichment for a Green-A Supabase tenant candidate CSV. "
            "Writes only SearchBug phone hits to lead_contacts with DNC unknown. "
            "Does not call GHL, Instantly, Bland, SMS, or any outreach service."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("tmp/supabase_green_a_enrichment_candidates_clean_2026-05-20.csv"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tmp/searchbug_green_a_live_2026-05-20/structured_results.csv"),
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume-from-output", action="store_true")
    parser.add_argument("--yes-spend-credits", action="store_true")
    args = parser.parse_args(argv)

    if not args.yes_spend_credits:
        parser.error("--yes-spend-credits is required because this calls SearchBug")

    load_dotenv(dotenv_path=Path(".env"))
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

    with args.input.open(newline="", encoding="utf-8") as fh:
        candidates = list(csv.DictReader(fh))
    if args.limit > 0:
        candidates = candidates[: args.limit]

    results: list[dict[str, Any]] = []
    if args.resume_from_output and args.output.exists():
        with args.output.open(newline="", encoding="utf-8") as fh:
            results = list(csv.DictReader(fh))

    for index, row in enumerate(candidates, start=1):
        if args.resume_from_output and already_processed(row["case_number"], results):
            continue
        result = await enrich_row(row, index)
        if result["status"] == "phone_found":
            result["persisted"] = persist_hit(client, result)
        results.append(result)
        write_csv(args.output, results)
        print(
            f"[{index}/{len(candidates)}] {result['case_number']} "
            f"{result['tenant_name']} -> {result['status']} "
            f"{result['address_match']}"
            f"{' persisted' if result['persisted'] else ''}",
            flush=True,
        )
        if result["status"] == "account_error":
            print("Stopping on SearchBug account/balance error.", flush=True)
            break

    summary = {
        "rows_recorded": len(results),
        "phone_found": sum(1 for r in results if r["status"] == "phone_found"),
        "persisted": sum(1 for r in results if str(r.get("persisted")).lower() == "true"),
        "account_errors": sum(1 for r in results if r["status"] == "account_error"),
        "api_errors": sum(1 for r in results if r["status"] == "api_error"),
        "http_errors": sum(1 for r in results if r["status"] == "http_error"),
        "ambiguous": sum(1 for r in results if r["status"] == "ambiguous"),
        "name_mismatch": sum(1 for r in results if r["status"] == "name_mismatch"),
        "no_records": sum(1 for r in results if r["status"] == "no_records"),
        "no_phone": sum(1 for r in results if r["status"] == "no_phone"),
        "strong_or_near_matches": sum(
            1
            for r in results
            if r["status"] == "phone_found"
            and r["address_match"] in {"exact", "same_street", "near_street"}
        ),
    }
    summary_path = args.output.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
