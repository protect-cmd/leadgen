"""Cosner Drake — SearchBug enrichment (manual-run only).

    python -m jobs.run_cd_enrich --dry-run --limit 5
    python -m jobs.run_cd_enrich --limit 50

Enriches stored cosner_filings (phone lookup via SearchBug, Answer-window gated).
The GHL push + Bland dialer are a later step (run_cd_outreach), paused on the
Cosner Drake GHL subaccount (Jonas) and call/SMS script (Chris). Writes only
cosner_filings.
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from services.cd_enrich import enrich_batch

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("cd.enrich")


async def main(args: argparse.Namespace) -> None:
    if args.dry_run:
        log.info("=== DRY RUN — no SearchBug calls, no DB writes ===")
    metrics = await enrich_batch(limit=args.limit, dry_run=args.dry_run,
                                 max_found=args.max_found)
    log.info("CD enrich metrics: %s", metrics)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Cosner Drake SearchBug enrichment")
    ap.add_argument("--dry-run", action="store_true", help="Print only; no SearchBug/DB writes")
    ap.add_argument("--limit", type=int, default=50, help="Max filings to enrich (default 50)")
    ap.add_argument("--max-found", type=int, default=None,
                    help="Stop after N paid phone hits (SearchBug $ budget cap)")
    asyncio.run(main(ap.parse_args()))
