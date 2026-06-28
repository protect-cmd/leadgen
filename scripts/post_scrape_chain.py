"""Ordered post-scrape chain: flag enrichable -> normalize court_date -> backfill rent.

Runs once daily after the scrapers (see services/daily_scheduler.py). Each step is
fault-isolated so one failure never blocks the rest; the process exit code is
non-zero if any step failed, so the scheduler logs surface it.

Only backfill_rent spends money (Rentometer, metered). It is OFF by default
(RENT_BACKFILL_DAILY_CAP=0). Set RENT_BACKFILL_DAILY_CAP=<n> on Railway to opt in
to <n> yield-targeted Rentometer calls/day once credits allow.

Usage:
    python scripts/post_scrape_chain.py
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("post_scrape_chain")


def _flag() -> None:
    from scripts.flag_enrichable import flag
    res = flag(only_null=True)
    log.info("flag_enrichable: %s", res)


def _normalize() -> None:
    from scripts.normalize_court_date import main as normalize_main
    normalize_main()


def _backfill_rent(cap: int) -> None:
    if cap <= 0:
        log.info("rent backfill skipped (RENT_BACKFILL_DAILY_CAP=%s)", cap)
        return
    from scripts.backfill_rent import main as rent_main
    rent_main(["--track", "both", "--cap", str(cap)])


def _health() -> None:
    """Run the pipeline health checks and push a Pushover summary. This is what
    makes monitoring reliable: it runs every day at the end of the post-scrape
    chain instead of being a manual one-shot."""
    import asyncio
    from scripts.verify_pipeline_health import notify_health
    fails = asyncio.run(notify_health())
    log.info("pipeline health: %s FAIL", fails)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    cap = int(os.getenv("RENT_BACKFILL_DAILY_CAP", "0"))
    steps = (("flag", _flag), ("normalize", _normalize),
             ("rent", lambda: _backfill_rent(cap)), ("health", _health))
    failed = 0
    for name, fn in steps:
        try:
            fn()
        except Exception:
            failed += 1
            log.exception("post-scrape step %r failed", name)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
