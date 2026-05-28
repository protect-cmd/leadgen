# Pipeline Gold Standard (tenant track)

A scraper stays on the cron schedule only if every layer below meets its bar.
Reference; full reasoning in `docs/superpowers/specs/2026-05-29-pipeline-gold-standard-design.md`.

## Layer 1 — Scraper

Required `Filing` fields (non-null, non-placeholder): `case_number`,
`tenant_name`, `property_address` (must contain a digit + a 5-digit ZIP),
`landlord_name`, `filing_date` (within last 14 days), `state`, `county`,
`notice_type`, `source_url`.

Optional: `court_date`, `claim_amount`, `property_type_hint`.

**Pass-rate bar:** >=85% of the last 100 filings pass `gate_address` AND
`gate_name` *without* LLM recovery. LLM is a safety net, not load-bearing.

**Runtime budget:** <=20 minutes per county for a 2-day lookback.

**Failure handling:** detect portal maintenance and raise a specific
exception; set `scraper.last_error` for the cron job's error handler.

## Layer 2 — Gates

Existing 9-gate filter in `pipeline/gates.py` is the contract. Each
rejection increments a named `run_metrics` counter. `gate_address` and
`gate_name` get LLM rescue when `LLM_RECOVERY_ENABLED=true`.

## Layer 3 — SearchBug

- Every qualifying lead reaches `search_tenant_detailed()` and persists
  `searchbug_status` to `lead_contacts`.
- `SEARCHBUG_DAILY_CAP` >= `expected_daily_volume x 1.5`.
- Circuit breaker on billing errors -> high-priority Pushover (built).

## Layer 4 — GHL push

| SearchBug status | Routing |
|------------------|---------|
| `phone_found` | `GHL_NG_NEW_FILING_STAGE_ID` (or `_COMMERCIAL_STAGE_ID`) + Instantly + Bland |
| `name_mismatch` / `ambiguous` | `GHL_NG_REVIEW_STAGE_ID`; skip Instantly + Bland |
| `no_records` / `no_phone` / `invalid_name` / `account_error` | drop, no push |

**Required env vars:** `GHL_API_KEY`, `GHL_NG_LOCATION_ID`,
`GHL_NG_NEW_FILING_STAGE_ID`, `GHL_NG_REVIEW_STAGE_ID`. The review stage
ID is hard-required — missing it silently drops every review-lane lead.

## Layer 5 — Observability

One Pushover summary per county per run, including all `gate_*` counters,
`searchbug_calls`, `gate_llm_recovered`, `ng_phones_pushed`,
`ng_review_pushed`. All counters persist to `run_metrics` (post-migration
013). Errors at any layer fire `send_job_error`.

## Quick checks

Run `python scripts/verify_pipeline_health.py` before every prod change.
It exits non-zero on FAIL.
