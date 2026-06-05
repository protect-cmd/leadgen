"""
Standalone runner for Galveston TX JP eviction scraper.

Usage:
    python -m jobs.run_galveston                # last 7 days, with case detail
    python -m jobs.run_galveston --no-detail    # last 7 days, no case detail (fast)
    python -m jobs.run_galveston --days 14      # last 14 days
    python -m jobs.run_galveston --date-from 06/01/2026 --date-to 06/07/2026

Outputs JSON to stdout (filings + errors + metadata). Designed to be:
  - Run directly for ad-hoc / smoke testing (e.g. Railway preview deploy)
  - Piped to Supabase insert/dedupe via `services.dedup_service` in
    production (TODO marker at the end - boss to wire up the standard
    insert pattern used by other county runners)

Exit codes:
    0  Success (filings scraped, or empty but no errors)
    1  Errors and no filings (full failure)
    2  CLI argument error
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta

from scrapers.texas.galveston import (
    GalvestonTXJPScraper,
    STATE,
    COUNTY,
    NOTICE_TYPE,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("galveston_runner")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Scrape Galveston County TX JP eviction filings",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days back from today (default 7). Ignored if "
             "--date-from / --date-to are provided.",
    )
    parser.add_argument(
        "--date-from",
        type=str,
        default=None,
        help="Explicit start date MM/DD/YYYY (use with --date-to).",
    )
    parser.add_argument(
        "--date-to",
        type=str,
        default=None,
        help="Explicit end date MM/DD/YYYY (use with --date-from).",
    )
    parser.add_argument(
        "--no-detail",
        action="store_true",
        help="Skip case detail page visits (faster; no address or "
             "judgment amount extracted).",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output (indented).",
    )
    return parser.parse_args()


def resolve_date_range(args):
    """Resolve --days vs explicit --date-from/--date-to."""
    if args.date_from and args.date_to:
        return args.date_from, args.date_to
    if args.date_from or args.date_to:
        log.error("--date-from and --date-to must be used together")
        sys.exit(2)
    end = datetime.now()
    start = end - timedelta(days=args.days)
    return start.strftime("%m/%d/%Y"), end.strftime("%m/%d/%Y")


def main():
    args = parse_args()
    date_from, date_to = resolve_date_range(args)

    log.info(
        "Starting Galveston TX scrape: %s -> %s (fetch_details=%s)",
        date_from, date_to, not args.no_detail,
    )

    scraper = GalvestonTXJPScraper()

    try:
        filings = scraper.scrape_all_judges(
            date_from=date_from,
            date_to=date_to,
            fetch_details=not args.no_detail,
        )
    except Exception as e:
        log.exception("Fatal scraper error: %s", e)
        return 1

    log.info("Scraped %d filings", len(filings))

    if scraper.errors_per_judge:
        log.warning("Per-judge errors: %s", scraper.errors_per_judge)

    output = {
        "state": STATE,
        "county": COUNTY,
        "notice_type": NOTICE_TYPE,
        "date_from": date_from,
        "date_to": date_to,
        "scrape_started_at": datetime.utcnow().isoformat() + "Z",
        "filing_count": len(filings),
        "errors_per_judge": scraper.errors_per_judge,
        "filings": filings,
    }

    if args.pretty:
        print(json.dumps(output, default=str, indent=2))
    else:
        print(json.dumps(output, default=str))

    # TODO(galveston-supabase-wiring): wire up Supabase insert+dedupe via
    # services.dedup_service to match pattern used in jobs/run_ohio.py and
    # other county runners. Until then this runner just emits JSON to stdout
    # for downstream processing or piping to a Supabase upsert step.
    # Boss to confirm preferred Filing-model mapping and dedup_service
    # function signature before adding the import.

    if not filings and scraper.errors_per_judge:
        log.error("Run failed - no filings and errors present")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())