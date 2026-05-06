# AGENTS.md

## Purpose

This repo powers the eviction/legal-services lead pipeline for Grant Ellis Group and Vantage Defense Group. It scrapes public court portals, deduplicates filings in Supabase, enriches contacts through BatchData, applies DNC/outreach safety gates, creates or queues contacts for GHL/Bland workflows, and exposes a small Railway dashboard for review/approval.

This file is the repo-level operating manual for Codex and other coding agents. Keep it concise, current, and focused on rules that should survive across sessions.

## Brand Rules

- Grant Ellis Group is the landlord/document-prep brand.
- Vantage Defense Group is the tenant/legal-defense brand.
- Do not introduce old brand names in code, dashboard labels, GHL labels, Bland scripts, Instantly sequences, docs intended for production, or automation copy.
- Replace old names everywhere before go-live:
  - EvictionCommand -> Grant Ellis Group
  - Nobles and Greyson / Nobles & Greyson -> Vantage Defense Group
- It is acceptable for legacy filenames, repo paths, or historical docs to still contain old names when renaming them would create unnecessary churn. User-facing and automation-facing text must use the new names.

## Safety And Compliance Guardrails

- Phone outreach must fail closed unless DNC status is explicitly clear.
- Treat `dnc_status = "clear"` as callable, `blocked` as blocked, and `unknown` as manual review only.
- Bland.ai auto-calling must remain disabled unless the user explicitly asks to enable it. The env flag is `AUTO_BLAND_CALLS_ENABLED`.
- Pushover phone alerts are optional and controlled by `PUSHOVER_ENABLED`, `PUSHOVER_APP_TOKEN`, and `PUSHOVER_USER_KEY`. Alert failures must never crash a job.
- GHL/dashboard approval must block Bland for missing phone numbers or non-clear DNC status.
- SMS sequences must include STOP opt-out language.
- Do not send or trigger production outreach during local tests unless the user explicitly approves it.
- Do not store secrets in code, docs, tests, Obsidian notes, or screenshots. Refer to env var names only.
- Compliance notes are implementation guardrails, not legal advice. Flag TCPA/A2P/DNC risk clearly when workflows involve calls or SMS.

## Current Workflow Shape

Expected production flow:

1. State/county job runs from Railway or local command.
2. Scraper returns normalized `Filing` records.
3. Runner checks Supabase for duplicate `case_number`.
4. New filings are inserted immediately to prevent repeat processing.
5. Address is optionally normalized.
6. Lead is classified before enrichment when scraper-provided fields allow it.
7. BatchData enriches contacts:
   - EC/Grant Ellis Group track uses property skip trace for landlord/owner.
   - NG/Vantage Defense Group track uses tenant people search when enabled.
   - Property lookup is shared when property type is missing.
8. DNC metadata from selected BatchData phone is stored on the filing.
9. GHL contact is created in the correct pipeline/stage.
10. Bland is queued for manual review unless auto-calling is explicitly enabled and DNC is clear.
11. Dashboard shows DNC, phone, classification, GHL, and Bland status for review.

## BatchData Optimization Rules

- Avoid duplicate property lookup calls. If both landlord and tenant tracks need property info, call `lookup_property_info` once and pass the result into both enrichment calls.
- If a scraper already provides `property_type_hint`, skip the BatchData property lookup.
- Expected BatchData call counts:
  - GEG only with property type present: 1
  - GEG only with missing property type: 2
  - GEG + VDG with property type present: 2
  - GEG + VDG with missing property type: 3
- DNC status currently comes from the selected BatchData phone record. A scrub vendor or official registry workflow can replace or supplement this later.
- The FTC DNC Reported Calls Data API is for complaint/reporting data, not pre-call lead scrubbing. Do not treat it as a scrub API.

## Scraper Notes

- Prefer a 2-day rolling lookback for daily scheduled jobs. It catches missed runs, timezone drift, and portal delays without creating excessive duplicates.
- Rent thresholds are state-specific: TX $1,500; TN $1,600; GA $1,600; FL $1,800; IL $1,800; WA $1,900; AZ $1,500; NV $1,600.
- California LA currently appears portal/default-date dependent and is blocked by data-source/access limitations. Do not assume fresh LA filings are available without confirming the source.
- Texas Harris:
  - Uses Harris County JP Public Extracts.
  - Select case type by visible text `Eviction`, not the first non-zero option.
  - CSV can provide claim amount and property type hints, reducing BatchData calls.
  - Portal may show a maintenance page until approximately 05:00 local portal time; if that happens, do not run the full pipeline.
- Indiana Marion currently uses a 2-day lookback.
- Georgia re:SearchGA currently uses a 2-day lookback.
- South Carolina Richland currently uses a 2-day lookback but has known blocker notes in `docs/blocker_sc_richland.md`.
- Tennessee Davidson currently uses a 2-day lookback and may also account for near-future docket/court dates in scraper logic.
- Check `docs/portal_notes.md` before changing portal selectors or date logic.

## Supabase And Migrations

- Migrations live in `migrations/`.
- Apply migrations before deploying code that reads new columns.
- `006_dnc_status.sql` adds DNC-related fields used by the dashboard and dedup service. If it is not applied, `/api/leads` can fail when selecting DNC columns.
- Do not assume a migration has been applied just because the file exists. Verify with Supabase or ask the user if needed.

## Railway And Deployment

- `railway.toml` controls the active scheduled job. Changing it can switch production cron behavior.
- Do not include unrelated `railway.toml` changes in a dashboard-only or DNC-only deploy unless the user intentionally wants the production cron changed.
- Railway uses the repo state pushed to GitHub, so dashboard changes must be committed and pushed to appear there.
- Before pushing, check `git diff --stat`, `git diff`, and `git status --short` for unrelated dirty files.

## Testing Expectations

- Use `rg` for code search when available.
- Run focused tests for the changed behavior.
- Run `pytest -q` before claiming the whole workflow is ready.
- Use `python scripts/smoke_scrapers.py --states texas,tennessee --notify` for repeatable scraper-only smoke tests.
- Scraper smoke tests should avoid triggering Supabase/GHL/Bland/BatchData unless that is explicitly part of the requested test.
- User has pre-approved scraper-only smoke tests when needed. Do not call the pipeline runner, BatchData enrichment, GHL, Bland.ai, or other production outreach/state-changing services during those tests without asking for explicit permission first.
- For Texas/Harris, scraper-only smoke tests are preferred while the portal is unstable.

## Local Context And Memory

- Use this `AGENTS.md` for standing repo instructions.
- Use Obsidian/Markdown notes for durable project memory, session logs, decisions, and next actions.
- Suggested local vault path: `C:\Users\Zeann\OneDrive\Documents\Obsidian Vault`.
- Do not put secrets into Obsidian. Store only env var names, service names, and configuration locations.

## Important Files

- `pipeline/runner.py` orchestrates dedup, classification, enrichment, GHL, DNC, and Bland status.
- `services/batchdata_service.py` handles BatchData enrichment and DNC metadata from selected phones.
- `services/dnc_service.py` is the central callability gate.
- `services/dedup_service.py` persists filings, enrichment, DNC fields, GHL IDs, and Bland status.
- `services/ghl_service.py` creates contacts and maps custom fields.
- `services/bland_service.py` builds Bland voicemail payloads/scripts.
- `dashboard/main.py` and `dashboard/index.html` power the Railway dashboard.
- `jobs/run_*.py` are scheduled entry points.
- `scrapers/*` contain one scraper per jurisdiction.
- `docs/ghl_sms_dnc_build_guide.md` captures current GHL SMS/DNC setup guidance.
- `docs/bland_ec_setup.md` captures Grant Ellis Group Bland setup notes.
- `docs/portal_notes.md` captures portal quirks and selector notes.

## Session Start Checklist

At the start of a meaningful coding session:

1. Read this file.
2. Run `git status --short`.
3. Check for uncommitted user changes before editing.
4. Inspect relevant files before changing them.
5. Verify whether recent migrations/deploy assumptions are true.
6. Keep edits tightly scoped to the user's request.

## Status Notes As Of 2026-05-06

- Last known clean pushed commit: `348214c feat: add dnc dashboard safeguards`.
- DNC dashboard safeguards and migration file exist; verify Supabase before deploying code that depends on DNC columns.
- BatchData enrichment has been optimized to share property lookup across tracks.
- Job lookbacks were moved toward a 2-day rolling window for scheduled states.
- Texas Harris scraper was corrected to choose the `Eviction` case type by text, but the portal was returning a maintenance page during the last test.
- There may be unrelated local dirty files in this workspace. Do not revert them without user approval.
