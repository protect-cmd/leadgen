from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.filing import Filing
from scrapers.arizona.maricopa import MaricopaJusticeCourtScraper
from scrapers.arizona.maricopa_assessor import AddressMatchResult


def format_proof_rows(
    filings: list[Filing],
    matches_by_case: dict[str, AddressMatchResult],
) -> list[str]:
    rows: list[str] = []
    for filing in filings:
        match = matches_by_case.get(filing.case_number)
        status = match.status if match else "not_checked"
        apn = ""
        if match and match.records:
            apn = match.records[0].apn
        rows.append(
            " | ".join(
                [
                    filing.case_number,
                    status,
                    filing.landlord_name,
                    filing.tenant_name,
                    filing.property_address,
                    f"APN {apn}" if apn else "APN",
                ]
            )
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scraper-only Maricopa address proof. Does not call runner, BatchData, GHL, or Bland."
    )
    parser.add_argument("--max-cases", type=int, default=25)
    parser.add_argument("--lookback-days", type=int, default=7)
    args = parser.parse_args(argv)

    scraper = MaricopaJusticeCourtScraper(
        lookback_days=args.lookback_days,
        max_cases=args.max_cases,
        enrich_addresses=True,
    )
    filings = scraper.scrape()

    print(f"Maricopa address proof: {len(filings)} filings")
    print(
        "Address matches: "
        + ", ".join(f"{key}={value}" for key, value in scraper.address_match_counts.items())
    )
    for row in format_proof_rows(filings, scraper.address_matches_by_case):
        print(row)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
