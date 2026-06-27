"""
Standalone CLI runner for Fort Bend TX JP eviction scraper.

Usage:
    python -m jobs.run_fortbend                              # last 7 days, full detail
    python -m jobs.run_fortbend --no-petition                # fast preview (city/zip only, no street)
    python -m jobs.run_fortbend --days 14                    # custom lookback window
    python -m jobs.run_fortbend --date-from 06/01/2026 --date-to 06/09/2026

Outputs JSON to stdout with filings, errors, and run metadata.
Exit codes:
    0 - success (filings >= 0)
    1 - scrape errored AND zero filings returned
    2 - CLI argument error

Designed to run from Railway US deployment. Tylerpaw subdomain accessible
without geo-block but Cloudflare may apply to scraper-class clients - if
that surfaces in production, refactor to add playwright-stealth (currently
deferred since Cloudflare blocking observed only on main fortbendcountytx.gov,
not on the tylerpaw subdomain).

TODO(fortbend-supabase-wiring): wire services.dedup_service insert+dedupe
once boss confirms Filing-model mapping. Currently emits JSON to stdout
which is downstream-pipeable.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta

from scrapers.texas.fortbend import FortBendTXJPScraper


def parse_args(argv=None) -> argparse.Namespace:
    """Parse CLI arguments. Use argv=None to read from sys.argv."""
    parser = argparse.ArgumentParser(
        description="Scrape Fort Bend County TX JP eviction filings",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help=(
            "Number of days back from today (default 7). "
            "Ignored if --date-from / --date-to are provided."
        ),
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
        "--no-petition",
        action="store_true",
        help=(
            "Skip Original Petition PDF fetch + parse (faster preview; "
            "no street address extracted, only city/state/zip from case "
            "detail page partial-address layer)."
        ),
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output (indented).",
    )
    return parser.parse_args(argv)


def resolve_date_range(args: argparse.Namespace) -> tuple:
    """
    Resolve start/end dates from CLI args.

    Priority: explicit --date-from + --date-to pair, otherwise --days back.
    Returns (date_from, date_to) as MM/DD/YYYY strings.

    Raises:
        ValueError: if only one of --date-from / --date-to provided, or if
            either is provided but not parseable as MM/DD/YYYY.
    """
    if args.date_from and args.date_to:
        # Validate format - will raise ValueError if bad
        datetime.strptime(args.date_from, "%m/%d/%Y")
        datetime.strptime(args.date_to, "%m/%d/%Y")
        return args.date_from, args.date_to

    if args.date_from or args.date_to:
        raise ValueError(
            "Provide BOTH --date-from and --date-to together, or neither."
        )

    end = datetime.now()
    start = end - timedelta(days=args.days)
    return start.strftime("%m/%d/%Y"), end.strftime("%m/%d/%Y")


def main(argv=None) -> int:
    """CLI entry point. Returns process exit code."""
    try:
        args = parse_args(argv)
    except SystemExit as e:
        return int(e.code) if e.code is not None else 2

    try:
        date_from, date_to = resolve_date_range(args)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    fetch_petitions = not args.no_petition
    scraper = FortBendTXJPScraper()

    result = scraper.scrape_all(
        date_from=date_from,
        date_to=date_to,
        fetch_petitions=fetch_petitions,
    )

    # Attach run metadata
    result["run_metadata"] = {
        "scraper": "FortBendTXJPScraper",
        "state": "TX",
        "county": "Fort Bend",
        "notice_type": "Eviction",
        "date_from": date_from,
        "date_to": date_to,
        "fetch_petitions": fetch_petitions,
        "executed_at_utc": datetime.utcnow().isoformat() + "Z",
    }

    # TODO(fortbend-supabase-wiring): boss to wire services.dedup_service
    # insert + dedupe here once Filing-model mapping confirmed. For now,
    # JSON output is downstream-pipeable.

    if args.pretty:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, default=str))

    # Exit code logic
    if not result.get("ok") and not result.get("filings"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())