# Tarrant Rebuild — Spec 2b (stub)

**Date:** 2026-05-29
**Status:** Stub — captures the Spec 2 diagnosis that triggered the deschedule. Full design + plan to be done when work is picked up.

## Problem

Tarrant TX scraper (`scrapers/texas/tarrant.py`, scheduled via `run_tarrant.py --pipe`) was producing zero recent filings as of 2026-05-29.

The `scripts/diagnose_scraper_silence.py --scraper tarrant --lookback 7` run on 2026-05-29 surfaced the failure mode: **every per-case `CaseDetail.aspx` navigation through Bright Data fails with `ERR_TUNNEL_CONNECTION_FAILED`**. The case-list index page fetch succeeds (Bright Data routes that page fine), but every subsequent detail-page navigation drops the tunnel.

Sample failures:

```
Tarrant TX: CaseDetail 6039063 failed: Locator.click: Timeout 30000ms exceeded.
Tarrant TX: CaseDetail 6039381 failed: Page.go_back: net::ERR_TUNNEL_CONNECTION_FAILED
Tarrant TX: CaseDetail 6038902 failed: Page.go_back: net::ERR_TUNNEL_CONNECTION_FAILED
... (repeats for every case in the lookback window)
```

Classification per `diagnose_scraper_silence.classify_silence`: **connectivity**.

## Likely root causes (to investigate)

1. **Bright Data zone deactivation / billing** — the `scraping_browser1` zone may have lapsed or its IP pool has been blocked by the Tarrant Tyler portal
2. **Tyler portal anti-bot upgrades** — Tarrant uses `portal-txtarrant.tylertech.cloud`; Tyler has rolled out per-session bot detection that may detect the WS-CDP connection pattern
3. **Bright Data credentials rotated** — `BRIGHTDATA_SB_WS` env var may have stale credentials

## Deschedule action (taken in Spec 2)

The cron entry in `services/daily_scheduler.SCHEDULED_JOBS` was commented out 2026-05-29. The scraper code stays in `scrapers/texas/tarrant.py` for future work. The corresponding entry in `scripts/verify_pipeline_health.SCHEDULED_JOB_COUNTIES` was removed so the verifier stops surfacing this as a stale FLAG.

## Path forward (open)

When this work is picked up:

1. Test Bright Data credentials directly with a one-off browser session against `portal-txtarrant.tylertech.cloud`
2. If credentials are fine, evaluate alternatives:
   - Different Bright Data zone (residential vs datacenter)
   - Browserless.io or Playwright Cloud as Bright Data alternative
   - Investigate if Tarrant offers a no-portal export path (CSV download, RSS, etc.)
3. Decide: rebuild the scraper, switch proxy, or drop Tarrant entirely from the roadmap

## Related

- Source matrix: `docs/source_discovery_matrix.md` — Tarrant is rated `green` with Bright Data; this incident may require downgrading
- Spec 2 (parent): `docs/superpowers/specs/2026-05-29-current-schedule-hardening-design.md`
