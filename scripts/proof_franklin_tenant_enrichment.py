from __future__ import annotations

import argparse
import asyncio
import csv
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Awaitable, Callable

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from models.contact import EnrichedContact
from models.filing import Filing
from scrapers.ohio.franklin import FranklinCountyMunicipalScraper
from services import batchdata_service, dnc_service


EnrichFunc = Callable[[Filing], Awaitable[EnrichedContact]]


@dataclass(frozen=True)
class ProofRow:
    case_number: str
    tenant_name: str
    property_address: str
    landlord_name: str
    filing_date: str
    phone: str
    email: str
    dnc_status: str
    dnc_source: str
    callable: bool
    decision_reason: str
    source_url: str


@dataclass(frozen=True)
class ProofSummary:
    total: int
    phones_found: int
    callable: int
    dnc_blocked: int
    dnc_unknown: int


async def run_enrichment_proof(
    filings: list[Filing],
    enrich_func: EnrichFunc,
) -> list[ProofRow]:
    rows: list[ProofRow] = []
    for filing in filings:
        contact = await enrich_func(filing)
        decision = dnc_service.can_call(contact)
        rows.append(
            ProofRow(
                case_number=filing.case_number,
                tenant_name=filing.tenant_name,
                property_address=filing.property_address,
                landlord_name=filing.landlord_name,
                filing_date=filing.filing_date.isoformat(),
                phone=contact.phone or "",
                email=contact.email or "",
                dnc_status=decision.status,
                dnc_source=contact.dnc_source or "",
                callable=decision.allowed,
                decision_reason=decision.reason,
                source_url=filing.source_url,
            )
        )
    return rows


def build_summary(rows: list[ProofRow]) -> ProofSummary:
    phones_found = sum(1 for row in rows if row.phone)
    callable_count = sum(1 for row in rows if row.callable)
    dnc_blocked = sum(1 for row in rows if row.dnc_status == "blocked")
    dnc_unknown = sum(1 for row in rows if row.dnc_status == "unknown")
    return ProofSummary(
        total=len(rows),
        phones_found=phones_found,
        callable=callable_count,
        dnc_blocked=dnc_blocked,
        dnc_unknown=dnc_unknown,
    )


def format_summary_lines(summary: ProofSummary) -> list[str]:
    return [
        "Franklin tenant enrichment proof",
        f"Total checked: {summary.total}",
        f"Phones found: {summary.phones_found} ({_pct(summary.phones_found, summary.total)})",
        f"Callable DNC-clear phones: {summary.callable} ({_pct(summary.callable, summary.total)})",
        f"DNC blocked phones: {summary.dnc_blocked}",
        f"DNC unknown/no-phone: {summary.dnc_unknown}",
    ]


def write_csv(rows: list[ProofRow], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(ProofRow.__dataclass_fields__))
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)


def select_latest_filings(filings: list[Filing], max_cases: int) -> list[Filing]:
    latest = sorted(
        filings,
        key=lambda filing: (filing.filing_date, filing.case_number),
        reverse=True,
    )
    if max_cases <= 0:
        return latest
    return latest[:max_cases]


def _pct(part: int, total: int) -> str:
    if total <= 0:
        return "0.0%"
    return f"{(part / total) * 100:.1f}%"


async def _batchdata_tenant_enrich(filing: Filing) -> EnrichedContact:
    return await batchdata_service.enrich_tenant(
        filing,
        lookup_property_if_missing=False,
    )


async def main_async(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a paid BatchData tenant-phone proof for Franklin County OH. "
            "Does not call Supabase, GHL, Bland, or the pipeline runner."
        )
    )
    parser.add_argument("--max-cases", type=int, default=25)
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tmp/franklin_tenant_enrichment_proof.csv"),
    )
    parser.add_argument(
        "--yes-spend-credits",
        action="store_true",
        help="Required because this proof calls BatchData once per sampled filing.",
    )
    args = parser.parse_args(argv)

    if not args.yes_spend_credits:
        parser.error("--yes-spend-credits is required because this calls BatchData")

    load_dotenv()

    scraper = FranklinCountyMunicipalScraper(lookback_days=args.lookback_days)
    filings = select_latest_filings(scraper.scrape(), args.max_cases)
    rows = await run_enrichment_proof(filings, _batchdata_tenant_enrich)
    write_csv(rows, args.output)

    for line in format_summary_lines(build_summary(rows)):
        print(line)
    print(f"CSV written: {args.output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
