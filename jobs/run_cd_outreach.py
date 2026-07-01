"""Cosner Drake — outreach orchestrator (manual-run only).

    python -m jobs.run_cd_outreach --dry-run --limit 5
    python -m jobs.run_cd_outreach --enrich-only --limit 50
    python -m jobs.run_cd_outreach --ghl-only --limit 20
    python -m jobs.run_cd_outreach --skip-bland --limit 20
    python -m jobs.run_cd_outreach --limit 20        # full chain

Chain: SearchBug enrich -> GHL push (cosner-drake-lead, Cosner Drake Pipeline /
New Lead) -> Bland voicemail (DNC-gated, Answer-window-gated). Writes only
cosner_filings. NOT wired into daily_scheduler — run manually so SearchBug spend
and dialing are deliberate.
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from services.cd_enrich import enrich_batch
from services.cd_ghl import push_batch
from services.cd_bland import trigger_batch

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("cd.outreach")


async def main(args: argparse.Namespace) -> None:
    dry = args.dry_run
    limit = args.limit
    if dry:
        log.info("=== DRY RUN — no SearchBug calls, no GHL/Bland/DB writes ===")

    if not args.ghl_only and not args.bland_only:
        log.info("--- Step 1: SearchBug enrichment (limit=%d) ---", limit)
        log.info("Enrich metrics: %s", await enrich_batch(limit=limit, dry_run=dry))
        if args.enrich_only:
            return

    if not args.enrich_only and not args.bland_only:
        log.info("--- Step 2: GHL CD contact push (limit=%d) ---", limit)
        log.info("GHL metrics: %s", await push_batch(limit=limit, dry_run=dry))
        if args.ghl_only:
            return

    if not args.enrich_only and not args.ghl_only and not args.skip_bland:
        log.info("--- Step 3: Bland voicemail trigger (limit=%d) ---", limit)
        log.info("Bland metrics: %s", await trigger_batch(limit=limit, dry_run=dry))

    log.info("=== Cosner Drake outreach run complete ===")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Cosner Drake outreach orchestrator")
    ap.add_argument("--dry-run", action="store_true", help="Print only; no API writes")
    ap.add_argument("--limit", type=int, default=50, help="Max records per step (default 50)")
    ap.add_argument("--enrich-only", action="store_true", help="SearchBug enrichment only")
    ap.add_argument("--ghl-only", action="store_true", help="GHL push only")
    ap.add_argument("--bland-only", action="store_true", help="Bland trigger only")
    ap.add_argument("--skip-bland", action="store_true", help="Enrich + GHL but skip Bland")
    asyncio.run(main(ap.parse_args()))
