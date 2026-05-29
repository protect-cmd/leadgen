"""Diagnose why a scheduled scraper isn't producing filings.

For each requested scraper, runs it standalone with a wider lookback,
captures the result, and classifies the failure mode into one of:

    fixed_now       - scraper produced filings with good pass rate; not silent
    no_volume       - clean run, 0 filings (legitimate quiet period)
    connectivity    - exception during fetch (portal down, network, Bright Data)
    parsing         - fetch succeeded but extraction returned 0 filings
    format_mismatch - filings returned but >50% fail gate_address

Usage:
    python scripts/diagnose_scraper_silence.py --scraper tarrant
    python scripts/diagnose_scraper_silence.py --scraper cobb --lookback 14
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv


SCRAPER_FACTORIES: dict = {}


def _register_scrapers() -> None:
    """Lazy import so a broken scraper module doesn't crash the diagnostic."""
    global SCRAPER_FACTORIES
    if SCRAPER_FACTORIES:
        return
    try:
        from scrapers.texas.tarrant import TarrantCountyJPScraper
        SCRAPER_FACTORIES["tarrant"] = lambda lookback: TarrantCountyJPScraper(lookback_days=lookback)
    except Exception as e:
        SCRAPER_FACTORIES["tarrant"] = e
    try:
        from scrapers.georgia.cobb import CobbMagistrateCourtScraper
        SCRAPER_FACTORIES["cobb"] = lambda lookback: CobbMagistrateCourtScraper(lookback_days=lookback)
    except Exception as e:
        SCRAPER_FACTORIES["cobb"] = e


def classify_silence(
    *,
    filings_count: int,
    exception: BaseException | None,
    pass_rate: float,
) -> str:
    """Classify a scraper run into the gold-standard buckets."""
    if exception is not None:
        msg = str(exception).lower()
        if "pars" in msg or "extract" in msg or "selector" in msg:
            return "parsing"
        return "connectivity"
    if filings_count == 0:
        return "no_volume"
    if pass_rate < 0.5:
        return "format_mismatch"
    return "fixed_now"


def _compute_pass_rate(filings) -> float:
    """Use the same gate check the verifier uses."""
    from pipeline import gates
    if not filings:
        return 0.0
    passed = 0
    for f in filings:
        addr = getattr(f, "property_address", "") or ""
        name = getattr(f, "tenant_name", "") or ""
        if gates.gate_address(addr) and gates.gate_name(name):
            passed += 1
    return passed / len(filings)


async def _run_scraper(factory, lookback: int):
    """Run a scraper (sync or async) and return (filings, exception)."""
    try:
        scraper = factory(lookback)
        result = scraper.scrape()
        if inspect.isawaitable(result):
            result = await result
        return result or [], None
    except Exception as e:
        return [], e


async def main_async(scraper_names: list, lookback: int) -> int:
    load_dotenv()
    _register_scrapers()

    for name in scraper_names:
        print(f"\n=== {name} ===", flush=True)
        factory = SCRAPER_FACTORIES.get(name)
        if factory is None:
            print(f"  ERROR: unknown scraper {name!r}; known: {list(SCRAPER_FACTORIES)}", flush=True)
            continue
        if isinstance(factory, BaseException):
            print(f"  ERROR: scraper module failed to import: {factory!r}", flush=True)
            print(f"  -> class=connectivity (or rebuild needed)", flush=True)
            continue

        print(f"  running with lookback={lookback}d...", flush=True)
        filings, exc = await _run_scraper(factory, lookback)

        if exc is not None:
            print(f"  exception: {type(exc).__name__}: {exc}", flush=True)
            print("  --- traceback (last 5 lines) ---", flush=True)
            tb = "\n".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            print(tb[-1000:], flush=True)

        rate = _compute_pass_rate(filings)
        klass = classify_silence(filings_count=len(filings), exception=exc, pass_rate=rate)
        print(
            f"  result: filings={len(filings)}  gate_address+gate_name pass={100*rate:.0f}%  "
            f"class={klass}",
            flush=True,
        )

        if klass == "fixed_now":
            print("  -> scraper appears to be working now; no action needed.", flush=True)
        elif klass == "no_volume":
            print("  -> legitimate quiet period; leave scheduled.", flush=True)
        elif klass == "format_mismatch":
            print("  -> Maricopa-class issue; fix the scraper's address formatter.", flush=True)
        elif klass == "parsing":
            print("  -> selectors / extractor drift; inspect output above + portal.", flush=True)
        elif klass == "connectivity":
            print("  -> portal / network / Bright Data issue; check infra before assuming code bug.", flush=True)

    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scraper", action="append", default=[],
                   help="Scraper name to diagnose (tarrant, cobb). Repeatable.")
    p.add_argument("--lookback", type=int, default=7,
                   help="Lookback days (default 7).")
    args = p.parse_args()
    names = args.scraper or ["tarrant", "cobb"]
    return asyncio.run(main_async(names, args.lookback))


if __name__ == "__main__":
    raise SystemExit(main())
