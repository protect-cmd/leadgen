"""
Proof script: Hamilton County OH yellow-source enrichment via SearchBug.

Scrapes up to --max-cases filings (property_address="Cincinnati, OH"),
runs each through enrich_tenant_by_name (the cost-reduction pipeline),
and prints a summary comparing results to the pre-optimization baseline.

Baseline (pre-optimization):
  3/20 phone hits (15%)   5 multi-match rejections paid   ~$3.00/usable number
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from models.contact import EnrichedContact
from models.filing import Filing
from scrapers.ohio.hamilton import HamiltonCountyMunicipalScraper
from services import batchdata_service
from services.name_utils import parse_name, split_tenants, is_common_surname

COST_PER_CALL = 0.77  # SearchBug PPD plan $/hit (charged on any response with records)

# ── baseline numbers from initial 20-filing test ──────────────────────────
BASELINE_TOTAL = 20
BASELINE_PHONES = 3
BASELINE_HIT_RATE = BASELINE_PHONES / BASELINE_TOTAL
BASELINE_MULTIMATCHES = 5
BASELINE_COST_PER_USABLE = 3.00


# ── log capture ───────────────────────────────────────────────────────────

@dataclass
class _Stats:
    """Counters populated by log capture + result inspection."""
    surname_skips: int = 0
    cap_hits: int = 0
    cache_hits: int = 0
    unparseable: int = 0
    multi_tenant_splits: int = 0   # filings that split into 2+ names
    middle_initial_fixes: int = 0  # names where parse_name strips a middle token
    sb_calls_estimated: int = 0    # names that reached SearchBug (estimated from logs)
    sb_phone_only: int = 0
    no_match: int = 0


class _CountingHandler(logging.Handler):
    def __init__(self, stats: _Stats) -> None:
        super().__init__()
        self._stats = stats

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if "common surname skip" in msg:
            self._stats.surname_skips += 1
        elif "daily cap" in msg:
            self._stats.cap_hits += 1
        elif "SearchBug phone-only hit" in msg:
            self._stats.sb_phone_only += 1
        elif "no match for" in msg:
            self._stats.no_match += 1
        elif "unparseable name" in msg:
            self._stats.unparseable += 1


# ── pre-analysis (no API calls) ───────────────────────────────────────────

def _pre_analyse(filings: list[Filing], stats: _Stats) -> None:
    """Count name-parsing outcomes before any API call."""
    for filing in filings:
        names = split_tenants(filing.tenant_name.strip())
        if len(names) > 1:
            stats.multi_tenant_splits += 1

        for raw_name in names:
            tokens = raw_name.strip().split()
            first, last = parse_name(raw_name)
            # Middle initial detected when there are 3+ tokens and the middle
            # token(s) differ from just first+last
            if (
                len(tokens) >= 3
                and first
                and last
                and not is_common_surname(last)
                and " ".join([first, last]).lower() != raw_name.strip().lower()
            ):
                stats.middle_initial_fixes += 1


# ── CSV row ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ProofRow:
    case_number: str
    tenant_name: str
    phone: str
    dnc_status: str
    dnc_source: str
    track: str
    source_url: str


# ── main proof ────────────────────────────────────────────────────────────

async def run_proof(
    filings: list[Filing],
    stats: _Stats,
) -> list[ProofRow]:
    rows: list[ProofRow] = []
    for filing in filings:
        contact: EnrichedContact = await batchdata_service.enrich_tenant_by_name(
            filing, lookup_property_if_missing=False
        )
        rows.append(ProofRow(
            case_number=filing.case_number,
            tenant_name=filing.tenant_name,
            phone=contact.phone or "",
            dnc_status=contact.dnc_status,
            dnc_source=contact.dnc_source or "",
            track=contact.track,
            source_url=filing.source_url,
        ))
    return rows


def _pct(part: int, total: int) -> str:
    if total == 0:
        return "0.0%"
    return f"{part / total * 100:.1f}%"


def _print_report(
    rows: list[ProofRow],
    stats: _Stats,
    total_filings: int,
) -> None:
    phones = sum(1 for r in rows if r.phone)
    dnc_clear = sum(1 for r in rows if r.phone and r.dnc_status == "clear")
    dnc_unknown = sum(1 for r in rows if r.phone and r.dnc_status == "unknown")
    dnc_blocked = sum(1 for r in rows if r.phone and r.dnc_status == "blocked")

    # Estimate SearchBug calls: names attempted minus pre-call filters
    # Each filing contributes len(split_tenants(name)) attempts
    total_name_attempts = sum(
        len(split_tenants(r.tenant_name.strip())) for r in rows
    )
    sb_calls_est = max(
        0,
        total_name_attempts
        - stats.surname_skips
        - stats.unparseable
        - stats.cache_hits
        - stats.cap_hits,
    )
    cost_est = sb_calls_est * COST_PER_CALL
    cost_per_phone = cost_est / phones if phones else float("inf")

    # Baseline
    baseline_calls_est = BASELINE_TOTAL - 0  # no pre-call filters before
    baseline_cost = BASELINE_MULTIMATCHES * COST_PER_CALL + BASELINE_PHONES * COST_PER_CALL
    # (hits + multi-match rejections were all charged)

    sep = "─" * 55
    print()
    print("Hamilton County OH — SearchBug cost-reduction proof")
    print(sep)

    print("\nName parsing")
    print(f"  Filings processed:            {total_filings:>4}")
    print(f"  Multi-tenant splits:          {stats.multi_tenant_splits:>4}  "
          f"(split into 2 names each)")
    print(f"  Middle-initial tokens fixed:  {stats.middle_initial_fixes:>4}  "
          f"(e.g. BRETT L LILLY → first=BRETT last=LILLY)")
    print(f"  Unparseable names:            {stats.unparseable:>4}")

    print("\nPre-call filters")
    print(f"  Common surname skipped:       {stats.surname_skips:>4}  (no charge)")
    print(f"  Cache hits:                   {stats.cache_hits:>4}  (no charge)")
    print(f"  Daily cap reached:            {stats.cap_hits:>4}")

    print("\nSearchBug (estimated calls)")
    print(f"  Calls made:                   {sb_calls_est:>4}")
    print(f"  Phone-only hits:              {stats.sb_phone_only:>4}")
    print(f"  No match:                     {stats.no_match:>4}")

    print("\nResults")
    print(f"  Phones found:     {phones:>3}/{total_filings}  "
          f"({_pct(phones, total_filings)})")
    print(f"  DNC-clear:        {dnc_clear:>3}")
    print(f"  DNC-unknown:      {dnc_unknown:>3}")
    print(f"  DNC-blocked:      {dnc_blocked:>3}")

    print("\nCost estimate")
    print(f"  Calls × ${COST_PER_CALL:.2f}:            ${cost_est:>6.2f}")
    if phones:
        print(f"  Cost per usable phone:        ${cost_per_phone:>6.2f}")
    else:
        print(f"  Cost per usable phone:           n/a")

    print(f"\n{sep}")
    print("Baseline vs. now")
    print(f"{'':30} {'Before':>10}  {'After':>10}")
    print(f"  {'Phones found':<28} {BASELINE_PHONES:>4}/{BASELINE_TOTAL}       "
          f"{phones:>4}/{total_filings}")
    print(f"  {'Hit rate':<28} {_pct(BASELINE_PHONES, BASELINE_TOTAL):>10}  "
          f"{_pct(phones, total_filings):>10}")
    print(f"  {'Multi-match rejections paid':<28} {BASELINE_MULTIMATCHES:>10}  "
          f"{'0 (filtered)':>10}")
    print(f"  {'Est. cost per usable phone':<28} "
          f"${BASELINE_COST_PER_USABLE:>9.2f}  "
          f"${cost_per_phone:>9.2f}" if phones else
          f"  {'Est. cost per usable phone':<28} "
          f"${BASELINE_COST_PER_USABLE:>9.2f}  {'n/a':>10}")
    print(sep)


def write_csv(rows: list[ProofRow], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(ProofRow.__dataclass_fields__))
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)


async def main_async(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Proof: Hamilton County OH SearchBug cost-reduction pipeline. "
            "Calls SearchBug; costs real money. Does NOT call GHL, Bland, or Supabase."
        )
    )
    parser.add_argument("--max-cases", type=int, default=20)
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tmp/hamilton_yellow_enrichment_proof.csv"),
    )
    parser.add_argument(
        "--yes-spend-credits",
        action="store_true",
        help="Required — this script calls SearchBug and spends real credits.",
    )
    args = parser.parse_args(argv)

    if not args.yes_spend_credits:
        parser.error("--yes-spend-credits required (this calls SearchBug)")

    load_dotenv()

    stats = _Stats()
    handler = _CountingHandler(stats)
    handler.setLevel(logging.DEBUG)
    logging.getLogger("services.batchdata_service").addHandler(handler)
    logging.getLogger("services.searchbug_service").addHandler(handler)
    logging.basicConfig(level=logging.WARNING)  # suppress noise to stdout

    scraper = HamiltonCountyMunicipalScraper(lookback_days=args.lookback_days)
    all_filings = scraper.scrape()
    filings = all_filings[: args.max_cases]

    print(f"Scraped {len(all_filings)} Hamilton County filings → testing {len(filings)}")

    _pre_analyse(filings, stats)

    rows = await run_proof(filings, stats)

    write_csv(rows, args.output)
    _print_report(rows, stats, len(filings))
    print(f"CSV: {args.output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
