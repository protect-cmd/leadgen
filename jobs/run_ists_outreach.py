"""ISTS Sub-Project B — outreach orchestrator (manual-run only).

    python -m jobs.run_ists_outreach --dry-run --limit 5
    python -m jobs.run_ists_outreach --enrich-only --limit 50
    python -m jobs.run_ists_outreach --ghl-only --limit 20
    python -m jobs.run_ists_outreach --skip-bland --limit 20
    python -m jobs.run_ists_outreach --limit 20        # full chain

NOT wired into daily_scheduler. Writes only ists_judgments.
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from services.ists_enrich import enrich_batch
from services.ists_ghl import push_batch
from services.ists_bland import trigger_batch

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ists.outreach")


async def main(args: argparse.Namespace) -> None:
    dry = args.dry_run
    limit = args.limit

    if dry:
        log.info("=== DRY RUN — no API writes ===")

    # Step 1: SearchBug enrichment
    if not args.ghl_only and not args.bland_only:
        log.info("--- Step 1: SearchBug enrichment (limit=%d) ---", limit)
        enrich_metrics = await enrich_batch(limit=limit, dry_run=dry)
        log.info("Enrich metrics: %s", enrich_metrics)
        if args.enrich_only:
            return

    # Step 2: GHL contact push
    if not args.enrich_only and not args.bland_only:
        log.info("--- Step 2: GHL ISTS contact push (limit=%d) ---", limit)
        ghl_metrics = await push_batch(limit=limit, dry_run=dry)
        log.info("GHL metrics: %s", ghl_metrics)
        if args.ghl_only:
            return

    # Step 3: Bland W1 call trigger
    if not args.enrich_only and not args.ghl_only and not args.skip_bland:
        log.info("--- Step 3: Bland W1 call trigger (limit=%d) ---", limit)
        bland_metrics = await trigger_batch(limit=limit, dry_run=dry)
        log.info("Bland metrics: %s", bland_metrics)

    log.info("=== ISTS outreach run complete ===")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="ISTS outreach orchestrator")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would happen; no API writes")
    ap.add_argument("--limit", type=int, default=50,
                    help="Max records per step (default 50)")
    ap.add_argument("--enrich-only", action="store_true",
                    help="Run SearchBug enrichment only")
    ap.add_argument("--ghl-only", action="store_true",
                    help="Run GHL push only (skip enrich + Bland)")
    ap.add_argument("--bland-only", action="store_true",
                    help="Run Bland trigger only (skip enrich + GHL)")
    ap.add_argument("--skip-bland", action="store_true",
                    help="Run enrich + GHL but skip Bland")
    asyncio.run(main(ap.parse_args()))
