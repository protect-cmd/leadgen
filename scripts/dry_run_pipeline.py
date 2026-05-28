"""End-to-end pipeline dry-run: scraper -> qualification -> 9-gate filter -> LLM
recovery -> STOP before SearchBug. Prints a per-filing verdict so you can see
what the live runner would do without burning any SearchBug credits.

Usage:
    python scripts/dry_run_pipeline.py --scraper harris --lookback 2
    python scripts/dry_run_pipeline.py --scraper harris --bypass-zip-filter
    python scripts/dry_run_pipeline.py --scraper harris --lookback 7 --bypass-zip-filter
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from datetime import date as _date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scraper", default="harris", choices=["harris", "davidson"])
    parser.add_argument("--lookback", type=int, default=2,
                        help="Days to look back when scraping (default 2)")
    parser.add_argument("--bypass-zip-filter", action="store_true",
                        help="Treat off-allowlist ZIPs as approved (no captured bucket)")
    parser.add_argument("--max-filings", type=int, default=0,
                        help="Cap filings processed (0 = no cap)")
    return parser.parse_args(argv)


async def _get_scraper(name: str, lookback: int):
    if name == "harris":
        from scrapers.texas.harris import HarrisCountyScraper
        return HarrisCountyScraper(lookback_days=lookback)
    if name == "davidson":
        from scrapers.tennessee.davidson import DavidsonTNScraper
        return DavidsonTNScraper(lookback_days=lookback)
    raise ValueError(f"unknown scraper: {name}")


async def _run_scraper(scraper):
    """Davidson's scrape() is sync; Harris is async. Normalize the interface."""
    import inspect
    result = scraper.scrape()
    if inspect.isawaitable(result):
        return await result
    return result


def _print_filing_row(filing, verdict: str, detail: str = "") -> None:
    name = (filing.tenant_name or "")[:25]
    addr = (filing.property_address or "")[:55]
    print(f"  [{verdict:14s}] {filing.case_number:18s} {name:25s} | {addr}")
    if detail:
        print(f"  {'':16s}  {detail}")


async def _dry_run_one(
    filing,
    *,
    bypass_zip_filter: bool,
    today,
    enrichment_window_days: int,
    seen_queries: set[str],
    counts: Counter[str],
    llm_enabled: bool,
):
    from pipeline.qualification import classify_lead
    from pipeline import gates
    from services.name_utils import infer_property_type, parse_name
    from services.searchbug_service import query_street_address
    from pipeline.qualification import extract_property_zip
    from services import llm_recovery_service

    # Infer property type for residential vs commercial routing
    if filing.property_type_hint is None:
        filing.property_type_hint = infer_property_type(filing)

    # 1. Qualification - bucket assignment
    outcome = classify_lead(
        state=filing.state,
        property_address=filing.property_address,
        filing_date=filing.filing_date,
        property_type=filing.property_type_hint,
        estimated_rent=filing.claim_amount,
        today=today,
        capture_expanded=os.environ.get("CAPTURE_EXPANDED_ZIPS", "true").lower() == "true",
        bypass_zip_filter=bypass_zip_filter,
    )

    if outcome.lead_bucket == "discarded":
        counts[f"discarded ({outcome.discard_reason})"] += 1
        _print_filing_row(
            filing, "DISCARDED",
            f"reason={outcome.discard_reason} zip={outcome.property_zip}",
        )
        return
    if outcome.lead_bucket == "captured":
        counts["captured"] += 1
        _print_filing_row(
            filing, "CAPTURED",
            f"zip={outcome.property_zip} (off-allowlist, no enrichment)",
        )
        return
    if outcome.lead_bucket == "held":
        counts["held"] += 1
        _print_filing_row(
            filing, "HELD",
            f"reason=stale (7+ days) zip={outcome.property_zip}",
        )
        return

    # 2. 9-gate filter
    if not gates.gate_filing_window(filing.filing_date, today, enrichment_window_days):
        counts["gate_out_of_window"] += 1
        _print_filing_row(filing, "GATE: window", f"{(today - filing.filing_date).days}d old")
        return
    if not gates.gate_court_date(filing.court_date, today):
        counts["gate_overdue"] += 1
        _print_filing_row(filing, "GATE: overdue", f"court_date={filing.court_date}")
        return

    # Address gate (with LLM recovery if rejected)
    if not gates.gate_address(filing.property_address):
        recovered = False
        if llm_enabled:
            llm_result = await llm_recovery_service.recover(
                filing.tenant_name or "", filing.property_address or "", filing.state,
            )
            if (llm_result.confidence >= llm_recovery_service.RECOVERY_CONFIDENCE_THRESHOLD
                    and not llm_result.skip_reason
                    and llm_result.street and llm_result.zip):
                candidate = llm_result.formatted_address
                if gates.gate_address(candidate):
                    filing.property_address = candidate
                    counts["llm_recovered_address"] += 1
                    recovered = True
                    _print_filing_row(
                        filing, "LLM-RECOV addr",
                        f"conf={llm_result.confidence:.2f} -> {candidate!r}",
                    )
        if not recovered:
            counts["gate_invalid_address"] += 1
            _print_filing_row(filing, "GATE: bad addr", f"addr={filing.property_address!r}")
            return

    # Name gate (with LLM recovery if rejected)
    if not gates.gate_name(filing.tenant_name):
        recovered = False
        if llm_enabled:
            llm_result = await llm_recovery_service.recover(
                filing.tenant_name or "", filing.property_address or "", filing.state,
            )
            if (llm_result.confidence >= llm_recovery_service.RECOVERY_CONFIDENCE_THRESHOLD
                    and not llm_result.skip_reason
                    and llm_result.first and llm_result.last):
                candidate = llm_result.formatted_name
                if gates.gate_name(candidate):
                    filing.tenant_name = candidate
                    counts["llm_recovered_name"] += 1
                    recovered = True
                    _print_filing_row(
                        filing, "LLM-RECOV name",
                        f"conf={llm_result.confidence:.2f} -> {candidate!r}",
                    )
        if not recovered:
            counts["gate_bad_name"] += 1
            _print_filing_row(filing, "GATE: bad name", f"name={filing.tenant_name!r}")
            return

    # has_ng_phone check skipped - would require Supabase round-trip per filing.

    # In-run query dedup
    _first, _last = parse_name(filing.tenant_name)
    _street = query_street_address(filing.property_address)
    _zip = extract_property_zip(filing.property_address) or ""
    if not gates.gate_query_dedup(_first, _last, _street, _zip, seen_queries):
        counts["gate_duplicate_in_run"] += 1
        _print_filing_row(filing, "GATE: dup query", f"{_first} {_last} @ {_street}")
        return

    # All gates passed - would call SearchBug here
    counts["would_enrich"] += 1
    _print_filing_row(
        filing, "WOULD ENRICH",
        f"first={_first!r} last={_last!r} street={_street!r} zip={_zip!r}",
    )


async def main_async(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    load_dotenv()

    from services import llm_recovery_service

    llm_enabled = llm_recovery_service.is_enabled() and bool(os.environ.get("OPENROUTER_API_KEY"))

    print(f"=== Dry-run pipeline preview ===")
    print(f"Scraper:           {args.scraper}")
    print(f"Lookback:          {args.lookback} days")
    print(f"Bypass ZIP filter: {args.bypass_zip_filter}")
    print(f"LLM recovery:      {'enabled' if llm_enabled else 'disabled'}")
    print(f"Window (days):     {int(os.environ.get('ENRICHMENT_WINDOW_DAYS', '10'))}")
    print()

    scraper = await _get_scraper(args.scraper, args.lookback)
    print(f"Running {args.scraper} scraper...")
    filings = await _run_scraper(scraper)
    print(f"Scraper returned {len(filings)} filings.\n")

    if args.max_filings:
        filings = filings[: args.max_filings]
        print(f"Capped to {len(filings)} filings.\n")

    today = _date.today()
    counts: Counter[str] = Counter()
    seen_queries: set[str] = set()
    window_days = int(os.environ.get("ENRICHMENT_WINDOW_DAYS", "10"))

    print(f"{'VERDICT':16s} {'CASE':18s} {'TENANT':25s} | ADDRESS")
    print("-" * 110)

    for filing in filings:
        try:
            await _dry_run_one(
                filing,
                bypass_zip_filter=args.bypass_zip_filter,
                today=today,
                enrichment_window_days=window_days,
                seen_queries=seen_queries,
                counts=counts,
                llm_enabled=llm_enabled,
            )
        except Exception as e:
            counts["error"] += 1
            print(f"  [ERROR         ] {filing.case_number:18s} - {e!r}")

    print()
    print("=" * 70)
    print(f"Summary ({len(filings)} filings scraped):")
    for bucket, n in sorted(counts.items(), key=lambda x: -x[1]):
        pct = 100 * n / max(1, len(filings))
        print(f"  {bucket:30s} {n:4d}  ({pct:.0f}%)")
    print()
    print(f"Would enrich (SearchBug calls if live): {counts.get('would_enrich', 0)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
