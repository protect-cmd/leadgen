"""Probe a court portal from YOUR egress IP and report reachability + anti-bot vendor.

This is the building-court-scrapers / reviewing-scraper-prs "Verify, don't ask
step 1" as a repeatable tool — run it before believing a builder's smoke numbers
or before diagnosing a `0 filings` scraper:

    python scripts/probe_portal.py hover.hillsboroughclerk.com
    python scripts/probe_portal.py https://www.example-court.gov/search clerk.othercounty.gov

A "BLOCKED via <vendor>" verdict tells you which rung of the bypass ladder you
need (see docs/context/scrapers.md). UNREACHABLE / timeout usually means an
IP-reputation block — retry on a US residential IP / Bright Data.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scrapers.antibot import probe


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    worst = 0
    for host in argv:
        result = probe(host)
        print(result.summary())
        if not result.reachable or result.blocked:
            worst = 1
    return worst


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
