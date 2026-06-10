"""Scrub the recently-enriched HELD numbers (out-of-scope area codes) through
DNCScrub to measure how many the API converts from 'held' -> callable/dnc.

Gathers Vantage (lead_contacts ng) + ISTS (ists_judgments) phones enriched in the
last --days whose area code has NO local DNC file, then runs dnc_service.verdict_many.

Requires DNCSCRUB_LOGIN_ID in env (else it just reports the held set and exits).

Usage:
    python scripts/test_dncscrub_held.py --days 5
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv

load_dotenv()
from supabase import create_client
from services import dnc_service

DNC_DIR = os.getenv("DNC_DIR", r"C:\Users\Zeann\Downloads\DNC Scrub")


def _covered() -> set[str]:
    return {os.path.basename(p).split("_")[1] for p in glob.glob(os.path.join(DNC_DIR, "*.txt"))}


def _held(phone: str, covered: set[str]) -> bool:
    d = "".join(c for c in (phone or "") if c.isdigit())
    if len(d) == 11 and d[0] == "1":
        d = d[1:]
    return len(d) == 10 and d[:3] not in covered


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=5)
    a = ap.parse_args(argv)

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    covered = _covered()
    since = (date.today() - timedelta(days=a.days)).isoformat()

    v = (sb.table("lead_contacts").select("phone").eq("track", "ng")
         .not_.is_("phone", "null").gte("updated_at", since).execute().data or [])
    i = (sb.table("ists_judgments").select("phone")
         .not_.is_("phone", "null").gte("enriched_at", since).execute().data or [])
    held = sorted({r["phone"] for r in (v + i) if _held(r["phone"], covered)})
    print(f"recent HELD numbers (last {a.days}d): {len(held)}")

    if not os.getenv("DNCSCRUB_LOGIN_ID"):
        print("\nDNCSCRUB_LOGIN_ID not set — add it to .env, then re-run to scrub these.")
        print("area codes:", dict(Counter(
            "".join(c for c in p if c.isdigit())[-10:][:3] for p in held).most_common(12)))
        return 0

    print("scrubbing via DNCScrub…", flush=True)
    verdicts = dnc_service.verdict_many(held)
    counts = Counter(verdicts.values())
    n = len(verdicts) or 1
    print(f"\n=== DNCScrub result on {len(verdicts)} held numbers ===")
    print(f"  callable (unlocked!): {counts.get('callable',0)}  ({100*counts.get('callable',0)//n}%)")
    print(f"  on DNC (correctly dropped): {counts.get('dnc',0)}  ({100*counts.get('dnc',0)//n}%)")
    print(f"\n  -> {counts.get('callable',0)} numbers we paid to enrich but couldn't dial are now callable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
