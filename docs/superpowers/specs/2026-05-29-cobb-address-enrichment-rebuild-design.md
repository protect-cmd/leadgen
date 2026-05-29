# Cobb Address Enrichment Rebuild — Spec 2c (stub)

**Date:** 2026-05-29
**Status:** Stub — captures the Spec 2 diagnosis that triggered the deschedule. Full design + plan to be done when work is picked up.

## Problem

Cobb GA Magistrate scraper (`scrapers/georgia/cobb.py`, scheduled via `run_georgia_cobb.py --pipe --notify`) was producing filings that passed the gate at only **4%** as of 2026-05-29.

The `scripts/diagnose_scraper_silence.py --scraper cobb --lookback 7` run on 2026-05-29 surfaced:
- 200 filings produced (good scraping volume)
- 4% pass rate on `gate_address` + `gate_name`
- Repeated `urllib3.exceptions.ProtocolError: ('Connection aborted.', RemoteDisconnected(...))` errors from Nominatim during address enrichment

Classification per `diagnose_scraper_silence.classify_silence`: **format_mismatch**.

## Root cause

Cobb is a **yellow source** per the source discovery matrix — the infax XML feed has tenant name + case number + court date but **no property address**. The current scraper enriches addresses via two chained lookups:

1. Cobb County Assessor → finds parcels by owner name (the landlord)
2. Nominatim → geocodes the assessor's `situs_addr` to attach `city` + `postcode`

Both steps are required for the resulting `property_address` to pass `gate_address` (which requires `\b[A-Z]{2}\s+\d{5}\b`). When Nominatim fails (rate-limited, transient connection drop, IP block), the address stays "Unknown" and the filing fails the gate.

The 4% pass rate corresponds to the small fraction of cases where:
- The landlord name owner-matches the assessor
- AND Nominatim happens to respond
- AND the geocoded result has a valid postcode

## Deschedule action (taken in Spec 2)

The cron entry in `services/daily_scheduler.SCHEDULED_JOBS` was commented out 2026-05-29. The scraper code stays in `scrapers/georgia/cobb.py` for future work. The corresponding entry in `scripts/verify_pipeline_health.SCHEDULED_JOB_COUNTIES` was removed so the verifier stops surfacing this as a stale FAIL.

## Path forward (open)

When this work is picked up, options to evaluate:

1. **Replace Nominatim with a paid geocoder** — e.g., Google Geocoding (already in use elsewhere via `services/geocode_service.py`), Mapbox, or HERE. Eliminates the rate-limit / connection-drop problem.
2. **Bypass geocoding entirely** — store the assessor's raw `situs_addr` + a hard-coded `GA` state + Cobb-County-specific ZIP map. Less accurate but always-on.
3. **Move Cobb to the Melissa-enrichment pool** when Melissa goes live (per the team's roadmap) — then it doesn't need scraper-side address enrichment at all.
4. **Drop Cobb** — accept that 4% yield isn't worth the operational complexity.

## Related

- Source matrix: `docs/source_discovery_matrix.md` — Cobb is rated `yellow` ("No property address in free sources")
- Spec 2 (parent): `docs/superpowers/specs/2026-05-29-current-schedule-hardening-design.md`
- Nominatim service: `services/nominatim_service.py`
