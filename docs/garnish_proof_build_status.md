# Garnish Proof — Build Status (paused to build Cosner Drake)

**Branch:** `feat/garnish-proof-vertical`
**Status as of 2026-06-23:** full pipeline built and tested; paused on external dependencies (Jonas GHL subaccount + Chris script sign-off). Resume from "What's left" below.

## What Garnish Proof is

Document-prep brand for consumers who already **lost or ignored a debt-collection lawsuit and had a judgment entered against them** (collection / garnishment imminent). Services: Claim of Exemption, Head of Household Exemption, Motion to Vacate Judgment, Hardship Declaration. The client is the judgment debtor.

## The source decision (why we trigger on the judgment, not the garnishment writ)

A full court-source audit (see memory `project_court_source_audit` / `project_garnish_proof_feasibility`) found **no source that is date-enumerable + address-complete + wage-garnishment, all three**:
- FL (Miami-Dade/Broward/Orange): reCAPTCHA-walled; Bright Data blocks govt sites; paid Broward API only.
- GA (re:SearchGA): garnishment is a discrete searchable case type, but addresses are redacted everywhere (even case detail / "No Documents").
- TX (Harris): wage garnishment legally barred (bank-account only); garnishment writs ~1/month in JP.
- OH (Hamilton): has a garnishment listing AND exposes addresses, but it's case-number lookup (not date-enumerable) and the whole site is Cloudflare-walled.

So we moved the trigger one step upstream to the **debt judgment**, which IS clean: **Harris JP "Judgments Entered" × "Debt Claim", default judgments**. Live dry-run: 215 default judgments in one window, **100% with the debtor's full home address**, real debt buyers (LVNV, Midland, Portfolio Recovery, Synchrony, Capital One). ~480 address-complete prime leads/month from Harris alone, scalable to other TX JP counties.

**TX product framing:** since TX bars wage garnishment, these judgments lead to bank-account seizure, so the script leans on **Motion to Vacate the Default Judgment + Claim of Exemption** (all four docs still apply; Motion to Vacate is the strongest fit for defaults).

## What's built (committed on the branch, 25 tests green)

| Piece | File | Notes |
|---|---|---|
| Table | `migrations/023_garnishment_orders.sql` | **applied to live DB** |
| Outreach columns | `migrations/024_garnishment_orders_outreach.sql` | **NOT applied yet** (ghl_contact_id, ghl_pushed_at, bland_call_id, bland_triggered_at) |
| Model | `models/garnishment.py` | `GarnishmentRecord`; GP-native column names (debtor_name, debtor_address, filing_date) |
| Scraper | `scrapers/texas/harris_debt_judgments.py` | reuses ISTS Harris machinery; casetype="debt claim"; default-judgment filter + `to_garnishment_record` mapper (vacate deadline = judgment + 30d) |
| Ingest job | `jobs/run_gp_harris.py` | `--dry-run` proven; live ingest not yet run |
| Store | `services/gp_store.py` | isolated, writes only garnishment_orders |
| Enrich | `services/gp_enrich.py` | SearchBug; 30-day freshness |
| GHL push | `services/gp_ghl.py` | mirror of ists_ghl; `garnish-proof-lead` tag; GHL_GP_* config |
| Bland | `services/gp_bland.py` | mirror of ists_bland; reuses shared dnc_service + ISTS call helpers; BLAND_GP_* config; track=garnish-proof |
| Outreach orchestrator | `jobs/run_gp_outreach.py` | enrich → GHL → Bland |

The outreach layer is a **config-swapped reuse of the ISTS pipeline** (shared dnc_service, searchbug_service, Bland helpers). Removed the dead Miami wage path (gp_classify, run_gp_miami).

## What's left to go live

1. **Apply migration 024** to the live DB (Supabase SQL editor; same as 023 was applied by hand).
2. **Live ingest:** `python -m jobs.run_gp_harris` (real write into garnishment_orders).
3. **Jonas:** create the Garnish Proof GHL subaccount; set `GHL_GP_LOCATION_ID`, `GHL_API_GP_KEY`, `GHL_GP_NEW_FILING_STAGE_ID`; paste GP custom-field UUIDs into `gp_ghl._FIELD_IDS`.
4. **Chris:** sign off the "Alex" judgment-framed Bland script + SMS copy; then set `BLAND_GP_AGENT_ID` / `BLAND_GP_SPANISH_AGENT_ID` / `BLAND_GP_PHONE_NUMBER` / `BLAND_GP_CALLBACK_PHONE_NUMBER`.
5. **Schedule:** wire `run_gp_harris` into `services/daily_scheduler.py` after enrichment + DNC are proven on real rows.

## Premium source still open (manual, via assistant Susan)

The actual **writ-of-garnishment** record (person being garnished *now*) is a better lead than the judgment stage. Susan is collecting these manually. Criteria given: writ filed/issued in last 30 days (NOT original case filing date), defendant is an individual with a home street address, skip business defendants / employer-only addresses. Find out which county/portal she's pulling from — if it has clean writ records with debtor addresses, it may be automatable later.
