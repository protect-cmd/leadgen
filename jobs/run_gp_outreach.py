"""Garnish Proof — outreach orchestrator (manual-run only).

    python -m jobs.run_gp_outreach --dry-run --limit 5
    python -m jobs.run_gp_outreach --enrich-only --limit 50
    python -m jobs.run_gp_outreach --ghl-only --limit 20
    python -m jobs.run_gp_outreach --skip-bland --limit 20
    python -m jobs.run_gp_outreach --limit 20        # full chain

Chain: SearchBug enrich -> GHL push (garnish-proof-lead) -> Bland voicemail
(DNC-gated). NOT wired into daily_scheduler. Writes only garnishment_orders.
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from services.gp_enrich import enrich_batch
from services.gp_ghl import push_batch
from services.gp_bland import trigger_batch

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("gp.outreach")


async def main(args: argparse.Namespace) -> None:
    dry = args.dry_run
    limit = args.limit

    if dry:
        log.info("=== DRY RUN — no API writes ===")

    if not args.ghl_only and not args.bland_only:
        log.info("--- Step 1: SearchBug enrichment (limit=%d) ---", limit)
        log.info("Enrich metrics: %s", await enrich_batch(limit=limit, dry_run=dry))
        if args.enrich_only:
            return

    if not args.enrich_only and not args.bland_only:
        log.info("--- Step 2: GHL GP contact push (limit=%d) ---", limit)
        log.info("GHL metrics: %s", await push_batch(limit=limit, dry_run=dry))
        if args.ghl_only:
            return

    if not args.enrich_only and not args.ghl_only and not args.skip_bland:
        log.info("--- Step 3: Bland voicemail trigger (limit=%d) ---", limit)
        log.info("Bland metrics: %s", await trigger_batch(limit=limit, dry_run=dry))

    log.info("=== Garnish Proof outreach run complete ===")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Garnish Proof outreach orchestrator")
    ap.add_argument("--dry-run", action="store_true", help="Print only; no API writes")
    ap.add_argument("--limit", type=int, default=50, help="Max records per step (default 50)")
    ap.add_argument("--enrich-only", action="store_true", help="SearchBug enrichment only")
    ap.add_argument("--ghl-only", action="store_true", help="GHL push only")
    ap.add_argument("--bland-only", action="store_true", help="Bland trigger only")
    ap.add_argument("--skip-bland", action="store_true", help="Enrich + GHL but skip Bland")
    asyncio.run(main(ap.parse_args()))
