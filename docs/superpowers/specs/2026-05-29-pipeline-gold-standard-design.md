# Pipeline Gold Standard — Tenant Track

**Date:** 2026-05-29
**Author:** Zee
**Status:** Draft (brainstormed 2026-05-29)

## Problem

The daily Railway cron runs across 7 counties and the operator (Zee) is still
finding silent failures, formatting quirks, and broken assumptions weeks after
the pipeline went live. Examples surfaced in the last 48 hours:

- Maricopa scraper builds addresses without a state abbreviation, causing 100%
  of its filings to fail `gate_address`. The LLM recovery layer accidentally
  rescues them — turn the LLM off and Maricopa silently produces zero leads.
- Cobb depends on an assessor lookup that succeeds maybe 20% of the time; the
  rest get dropped without a clear signal.
- `searchbug_status` was being computed correctly but silently stripped on
  Supabase write because the column didn't exist (fixed 2026-05-29 via
  migration 013, but the leak was invisible until inspection).
- 22 `name_mismatch`/`ambiguous` leads were dropped today because
  `GHL_NG_REVIEW_STAGE_ID` isn't set; no alert fired.
- The Harris portal had a maintenance window during a manual test and the
  scraper hit a generic 60s timeout instead of detecting the maintenance page
  and alerting clearly.

The throughline: **components fail to a quiet zero instead of failing loud.**
The operator has to manually inspect logs and Supabase to know whether a run
worked. This spec defines the bar each layer has to meet so the operator can
stop inspecting.

## Scope

This spec covers the **tenant track** (NG / Vantage Defense), specifically the
hop chain:

```
scraper → gate stack → SearchBug enrichment → GHL push
```

Out of scope (separate future specs):

- Spec 2: Bring current scrapers up to this gold standard (per-scraper diagnosis)
- Spec 3: Onboarding playbook for new scrapers as Nourul/Franz/Donnald deliver
- Spec 4: Bland call quality (transcripts, scripting, post-call analysis)
- Spec 5: Instantly campaign hygiene (deliverability, sequences)
- Spec 6: Unified caller-lookup dashboard — replaces the current
  multi-tab Lead Queue with a single search-driven view where callers
  can resolve a client by name, phone, case number, or address. Today's
  dashboard at `/dashboard/main.py` is segmented by brand/view/status
  filters and is too noisy for live call workflows.
- Dashboard UX (broader), manual review queue, captured-bucket promotion
- EC (landlord) track — currently paused

The Bland and Instantly **handoffs** are in scope (lead gets enrolled / queued
cleanly with status persisted); their internal quality is not.

## The five-layer gold standard

A scraper is production-green and stays on the cron schedule **only if every
layer below meets its bar**. Falling out of standard at any layer means the
scraper drops to the bench (code stays in repo, not on the schedule) until
the regression is fixed.

### Layer 1 — Scraper output

**Required fields on every `Filing` (must be non-null and non-placeholder):**

- `case_number` — must be unique within the (state, county) namespace
- `tenant_name` — non-empty, not a known placeholder ("Unknown", "John Doe")
- `property_address` — non-empty, not "Unknown", must contain a digit (street
  number) and a 5-digit ZIP
- `landlord_name` — non-empty
- `filing_date` — valid date, within the last 14 days of `today`
- `state` — 2-letter abbreviation matching the scraper's known state
- `county` — non-empty
- `notice_type` — non-empty (defaults to "Eviction" if portal doesn't expose)
- `source_url` — non-empty

**Conditional / optional fields:**

- `court_date` — populate only if portal exposes hearing date. Must be in the
  future or NULL (past dates auto-drop at `gate_court_date`)
- `claim_amount` — only if portal exposes rent in the extract (Harris CSV does;
  most others don't)
- `property_type_hint` — only if portal classifies cause-of-action as
  residential vs commercial

**Pass-rate requirement:**

≥**90%** of filings from a run must pass `gate_address` AND `gate_name`
**after LLM recovery**. Measured against the last 100 filings persisted to
Supabase, computed by `scripts/verify_pipeline_health.py`.

**Operational requirements:**

- Detects portal-down / maintenance page and raises a specific exception
  (e.g., `PortalMaintenanceError`) instead of a generic timeout. The cron
  job catches it and fires a Pushover alert tagged with the county.
- Sets `scraper.last_error: str | None` so the cron job's exception handler
  can route to `send_job_error`.
- Idempotent: re-running the scraper for the same date range produces the
  same set of `case_number` values (downstream dedup relies on this).
- Per-county runtime budget: **≤ 10 minutes** for a 2-day lookback. Longer
  runs trigger a Pushover warning (a slow run probably means portal
  degradation worth a human look).

### Layer 2 — Gate stack

The existing 9-gate filter in `pipeline/gates.py` and `pipeline/runner.py` is
the contract. No code changes required for Spec 1. The standard:

- Each gate that rejects increments a named `run_metrics` counter
  (`gate_out_of_window`, `gate_overdue`, `gate_invalid_address`,
  `gate_bad_name`, `gate_existing_phone`, `gate_duplicate_in_run`)
- `gate_address` and `gate_name` are the only gates that get LLM rescue. A
  successful rescue increments `gate_llm_recovered` and the recovered
  filing is mutated in place before re-running the gate.
- LLM recovery is **opt-in via `LLM_RECOVERY_ENABLED=true`**. When disabled,
  the pipeline must function correctly using only regex — i.e., the LLM is
  a safety net, never the load-bearing component. (See Layer 1: a scraper
  that only passes 90% **with** LLM recovery is a fragile scraper. Aim for
  ≥85% pass rate **without** LLM, then let LLM bring it over 90%.)

The classification function `classify_lead` stays as-is for now. Most of
its branches (zip_not_approved, captured, held, rent_below_threshold) are
landlord-era logic that the `BYPASS_ZIP_FILTER=true` flag effectively
neutralizes for tenant. Documented as such; not refactored in Spec 1.

### Layer 3 — SearchBug enrichment

- Every qualifying lead reaches `search_tenant_detailed()` with a real HTTP
  POST. Verified by `searchbug_status` being persisted to `lead_contacts`.
- `SEARCHBUG_DAILY_CAP` must be sized for `expected daily volume × 1.5`. As
  of 2026-05-29 the cap is 200 on Railway; with bypass enabled this is
  near-marginal and should be revisited when Spec 2 reports observed daily
  volume.
- The process-level circuit breaker trips on the first SearchBug
  account/billing error and fires a high-priority Pushover. No code changes
  needed — already built.
- The local sqlite daily-cap counter auto-resets at UTC midnight. No manual
  reset required day-to-day.

### Layer 4 — GHL push

- `phone_found` → `GHL_NG_NEW_FILING_STAGE_ID` (residential) or
  `GHL_NG_COMMERCIAL_STAGE_ID` (commercial property_type) + `Instantly`
  enrollment + `bland_status='pending'`
- `name_mismatch` or `ambiguous` → `GHL_NG_REVIEW_STAGE_ID` with a
  `Review-NameMismatch` or `Review-Ambiguous` tag. Instantly and Bland are
  **skipped** for review-lane leads.
- `no_records` / `no_phone` / `invalid_name` / `account_error` → drop, no
  GHL push, status persisted to `lead_contacts.searchbug_status` for audit.

**Required env vars** (pipeline fails loud if any are missing on a run
expected to need them):

- `GHL_API_KEY`, `GHL_NEW_FILING_STAGE_ID`, `GHL_NG_LOCATION_ID`,
  `GHL_NG_NEW_FILING_STAGE_ID`
- `GHL_NG_REVIEW_STAGE_ID` — **must be set** or every name_mismatch /
  ambiguous lead silently drops. The verifier script flags this as a hard
  error.
- `GHL_NG_COMMERCIAL_STAGE_ID` — required only if commercial property_type
  is observed in the run. If never observed, optional.

A GHL push failure (HTTP error, auth, missing stage) fires
`send_job_error` with `stage="ghl_create_ng"` or `stage="ghl_review_ng"`.

### Layer 5 — Observability

A green production run must produce exactly **one** Pushover summary per
county per scheduled execution, containing:

- Filings received, duplicates skipped, address-skipped, captured
- Each `gate_*` counter
- SearchBug calls (this run) + daily total
- Phones found, GHL created (auto vs review breakdown), Instantly enrolled,
  Bland triggered
- LLM-recovered count (if non-zero)
- Elapsed seconds

All of the above must also persist to `run_metrics` via the column-discovery
cache in `dedup_service.write_run_metrics()`. After migration 013, every
counter has a column.

Errors at any layer fire `send_job_error` with `(job, stage, error)`:

| Stage tag | Trigger |
|-----------|---------|
| `scraper_<county>` | Scraper raised, returned 0 filings unexpectedly, or hit maintenance |
| `batchdata_enrichment` | Enrichment threw |
| `ghl_create_ec` / `ghl_create_ng` | GHL contact creation HTTP failure |
| `ghl_review_ng` | GHL review-stage push failed |
| `bland_<track>` | Bland trigger failed (still counts as queued) |
| `write_run_metrics` | Supabase persistence failed |

Daily-cap and account-error alerts are throttled to once per day via
`claim_alert_once_today`. All other errors fire per occurrence.

## Deliverables

Spec 1 ships only documentation and a verifier script:

1. **`docs/pipeline_gold_standard.md`** — 1-2 page reference codifying the
   five-layer contract above for ongoing use. Cross-links to
   `source_discovery_matrix.md` for green/yellow/red source classification.

2. **`scripts/verify_pipeline_health.py`** — One-shot audit run, exits
   non-zero on any FAIL-level finding. Verifies:

   a. **Env vars** — all required env keys present, no stale ones
      (delegates to existing `scripts/verify_env_vars.py` and extends it
      with cross-layer checks like "if NG track enabled, NG_REVIEW_STAGE_ID
      must be set").

   b. **Schema** — migrations 012 + 013 applied; `lead_contacts` has the
      expected columns; `run_metrics` has every gate counter column.

   c. **Scheduled scrapers** — for each entry in
      `daily_scheduler.SCHEDULED_JOBS`, pull the last 100 filings from
      Supabase, compute pass rate against `gate_address` + `gate_name`,
      flag scrapers below 90%.

   d. **SearchBug headroom** — read `daily_cap` from the local cache (or
      the Railway cache via a future hook) and confirm `SEARCHBUG_DAILY_CAP
      × 0.6` headroom is available at the start of cron windows.

   e. **GHL stage IDs** — call the GHL API once per stage ID and confirm
      they resolve to actual pipeline stages (not 404).

   Output format mirrors `verify_env_vars.py`: per-check `OK`/`FLAG`/`FAIL`
   with file/line cross-references for fixes.

3. **No code changes** to `pipeline/`, `services/`, or `models/`.

## What this spec does NOT prescribe

- It does not say which scrapers must be removed from the schedule. That's
  Spec 2 — Spec 1 only defines the bar; Spec 2 applies it.
- It does not introduce new gates or refactor existing ones. The gate
  stack as of 2026-05-29 is the contract.
- It does not change Supabase schema. Migrations 012 + 013 are already
  applied.
- It does not address dashboard UX, manual review queue, or the captured
  bucket promotion workflow.

## Success criteria

Spec 1 is done when:

1. `docs/pipeline_gold_standard.md` exists and the operator (Zee) confirms it
   captures the intent.
2. `scripts/verify_pipeline_health.py` runs to completion in <30 seconds and
   reports per-layer status for the current production environment.
3. Running the verifier today (2026-05-29) reproduces the known issues we
   already discussed: Maricopa <90% pass rate, missing
   `GHL_NG_REVIEW_STAGE_ID`, etc. — proving the verifier is a useful
   instrument before Spec 2 starts fixing them.
