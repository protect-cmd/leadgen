"""Cosner Drake — outreach orchestrator (manual-run only).

    python -m jobs.run_cd_outreach --dry-run --limit 5
    python -m jobs.run_cd_outreach --enrich-only --limit 50
    python -m jobs.run_cd_outreach --ghl-only --limit 20
    python -m jobs.run_cd_outreach --limit 20        # enrich -> GHL push

Chain: SearchBug enrich -> GHL push (cosner-drake-lead, Cosner Drake Pipeline /
New Lead). Bland dialing is not wired yet (awaiting Chris's "you've been sued /
file your Answer" script + BLAND_CD_* config). Writes only cosner_filings.
NOT wired into daily_scheduler — run manually so SearchBug spend is deliberate.
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from services.cd_enrich import enrich_batch
from services.cd_ghl import push_batch

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("cd.outreach")


async def main(args: argparse.Namespace) -> None:
    dry = args.dry_run
    limit = args.limit
    if dry:
        log.info("=== DRY RUN — no SearchBug calls, no GHL/DB writes ===")

    if not args.ghl_only:
        log.info("--- Step 1: SearchBug enrichment (limit=%d) ---", limit)
        log.info("Enrich metrics: %s", await enrich_batch(limit=limit, dry_run=dry))
        if args.enrich_only:
            return

    if not args.enrich_only:
        log.info("--- Step 2: GHL CD contact push (limit=%d) ---", limit)
        log.info("GHL metrics: %s", await push_batch(limit=limit, dry_run=dry))

    log.info("=== Cosner Drake outreach run complete ===")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Cosner Drake outreach orchestrator")
    ap.add_argument("--dry-run", action="store_true", help="Print only; no API writes")
    ap.add_argument("--limit", type=int, default=50, help="Max records per step (default 50)")
    ap.add_argument("--enrich-only", action="store_true", help="SearchBug enrichment only")
    ap.add_argument("--ghl-only", action="store_true", help="GHL push only")
    asyncio.run(main(ap.parse_args()))
