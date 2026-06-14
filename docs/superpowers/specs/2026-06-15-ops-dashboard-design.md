# Ops Dashboard (`/ops`) — Design

**Date:** 2026-06-15
**Status:** Approved (brainstorm), pending implementation plan

## Goal

A single internal page that answers two questions at a glance:
1. **Is the automation healthy?** — did today's scrapers + post-scrape chain run, any stalls/failures, credits/caps remaining.
2. **How is the pipeline converting?** — the scraped → enrichable → rent≥$1600 → phone → callable → fired funnel, per track.

These numbers (scrape counts, funnel/yield, spend) exist in the DB but are **not visible** in `/search` or `/lists` today. Scope: ops heartbeat **and** funnel on one page; today's numbers as the headline with a 7-day trend for context; per-county scrape rows + per-track funnel.

## Audience & auth

Internal operators. Served behind the **existing dashboard auth** (`require_queue` dependency in `dashboard/main.py`) — nothing new to secure.

## Architecture (Approach A)

Mirrors the existing `/lists` and `/search` pattern:

- **`services/ops_stats.py`** (new) — all aggregation. One function per page section, each taking the Supabase client (and the enrichment cache for spend) and returning a plain dict. Pure data → unit-testable with a fake client. Each section is computed independently and wrapped so a failing query degrades that section to `{"error": ...}` rather than failing the page (mirrors the column-fallback pattern already in `dedup_service`/`queue_builder`).
- **`dashboard/main.py`** (modify) — add `GET /ops` (returns `ops.html` via `FileResponse`, `Depends(require_queue)`) and `GET /api/ops` (returns the aggregated JSON, `Depends(require_queue)`).
- **`dashboard/ops.html`** (new) — fetches `/api/ops` on load, renders the sections. Dark theme matching `lists.html`/`search.html`. A **Refresh** button re-fetches; no auto-refresh.
- **`tests/test_ops_stats.py`** (new) — unit tests for the funnel math, %-drop, health-flag thresholds, and per-section error isolation, using a fake Supabase client.

`/api/ops` returns one JSON object:
```json
{
  "as_of": "2026-06-15T16:00:00Z",
  "health": {"flags": [{"level":"red|warn|ok","msg":"..."}]},
  "scrapes": {"rows": [{"county","received","new","dupes","last_run","spark7":[...]}], "error": null},
  "spend":   {"searchbug_today": 73, "searchbug_cap": 100,
              "bland_today": 99, "bland_cap": 100,
              "rentometer_credits": 263, "rentometer_as_of": "2026-06-14T..."},
  "funnel":  {"vantage": {...stage counts...}, "ists": {...}},
  "trend":   {"filings":[...7], "phones":[...7], "fired":[...7]}
}
```

## Page sections (top → bottom)

### 1. Health flags
A banner of red/warn/ok chips. Computed in `ops_stats.health_flags()`, reusing the thresholds already in `scripts/verify_pipeline_health.py`:
- **Scraper dark** (red) — a scheduled county with no new `filings.scraped_at` (or `run_metrics`) in > 7 days. One chip per dark county.
- **Scraper missing today** (warn) — a scheduled county that usually runs but has no `run_metrics` row today.
- **Post-scrape chain didn't run today** (warn) — proxy: `max(filings.enrichable_checked_at)` is not today (the chain's `flag_enrichable` step stamps it).
- **SearchBug account error today** (red) — an entry in the cache `alert_dedupe` table for `searchbug_account_error` dated today.
- **Bland at daily cap** (warn) — bland daily counter ≥ `BLAND_DAILY_CAP`.
- **Rentometer credits low** (warn) — last-known credits < a threshold (env `RENTOMETER_LOW_CREDITS`, default 100).
- **DNCScrub not configured** (warn) — `DNCSCRUB_LOGIN_ID` unset (local-files-only fallback).
- If none: a single green "All systems nominal."

### 2. Today's scrapes (per-county)
Table from `run_metrics` (today) joined with 7-day history:
`county | received | new (received − duplicates) | duplicates | last_run (HH:MM) | 7-day received sparkline`.
Rows for each scheduled county (Harris, Davidson, Franklin, Maricopa, Hamilton). A county expected today but with no row is shown as **"missing"** (ties to the health flag).

### 3. Spend & caps (today)
One strip:
- **SearchBug**: calls today / `SEARCHBUG_DAILY_CAP` (from the cache `daily_cap` kind=`searchbug`).
- **Bland**: dials today / `BLAND_DAILY_CAP` (kind=`bland`).
- **Rentometer**: **last-known** `credits_remaining` + "as of" timestamp (see Design Decisions).

### 4. Funnel (Vantage | ISTS, side-by-side)
Stage counts over the active window (Vantage 21d on `filing_date`; ISTS 14d on `judgment_date`), each with % drop from the previous stage:

| Stage | Vantage source | ISTS source |
|---|---|---|
| Scraped | `filings` filing_date≥today−21 | `ists_judgments` (all judgments) |
| Enrichable / fresh | `filings.is_enrichable=TRUE` (in 21d) | judgments with judgment_date≥today−14 |
| Rent ≥ $1600 | `estimated_rent ≥ 1600` | `estimated_rent ≥ 1600` |
| Phone found | `lead_contacts` (ng) phone present | `ists_judgments.phone` present |
| Callable | `dnc_status='callable'` | `dnc_status='callable'` |
| Fired | `lead_contacts.bland_call_id` set | `ists_judgments.bland_call_id` set |
| Staged to GHL | `lead_contacts.ghl_contact_id` set | `ists_judgments.ghl_contact_id` set |

### 5. 7-day trend
Three sparklines (or a tiny table): **filings/day**, **phones-found/day**, and **fired/day**, totals across counties/tracks.
- filings/day, phones/day — from `run_metrics` (`filings_received`, `phones_found`), which has a `run_at` per row.
- fired/day — union of `lead_contacts.bland_triggered_at` (ng/Vantage, **new column** — see below) + `ists_judgments.bland_triggered_at` (already exists), bucketed by day.

**New: `lead_contacts.bland_triggered_at`.** `lead_contacts` currently has no per-fire timestamp, so this design adds one (additive, nullable `TIMESTAMPTZ`). It is set in `dedup_service.set_bland_status(...)` whenever a fire dispatches (`call_id` present).

**Pre-migration safety (critical):** `set_bland_status` writes via `_execute_optional_lead_contact_write`, which suppresses the *entire* `lead_contacts` update on any error. So `bland_triggered_at` must NOT be added to the payload blindly — on an environment without the column, the write would fail and silently drop `bland_call_id` too, breaking fire idempotency (leads would look undialed and re-fire). The implementation must gate the field through the existing `_lead_contact_known_columns()` discovery helper: only include `bland_triggered_at` when the column is present. This makes deploy order irrelevant (deploy code → apply migration → the field starts populating).

## Design decisions

1. **Rentometer credits = last-known, not live.** Fetching live spends 1 credit per page load. Instead, capture `credits_remaining` whenever a Rentometer call is made (the `/summary` response already returns it) and store the latest in a small key-value row in the enrichment-cache sqlite (which lives on the persistent volume). The dashboard reads that value + its timestamp. If never recorded, show "unknown — run a rent batch to populate." This adds a tiny `ops_kv` helper to `enrichment_cache` and one line in the rent call path to record it.
2. **"Chain ran today" is a proxy** via `max(filings.enrichable_checked_at) == today`. Accurate enough; a dedicated run marker can be added later if certainty is needed.
3. **Per-section fault isolation.** `/api/ops` always returns 200 with whatever sections succeeded; a failed section carries `"error"` and the page renders "unavailable" for it. One slow/broken query never blanks the whole dashboard.

## Error handling

- Each `ops_stats` section function catches its own exceptions and returns `{"error": "<short>"}`.
- `/api/ops` composes the sections; never 500s on a single section failure.
- `ops.html` renders an "unavailable" placeholder for any section with `error`.

## Refresh

On page load + a manual **Refresh** button. No auto-refresh (the user did not want a wall display).

## Testing

`tests/test_ops_stats.py` with a fake Supabase client:
- Funnel stage counts + %-drop math (Vantage and ISTS).
- Health-flag thresholds (dark scraper, missing-today, chain-not-run, at-cap, DNCScrub unset) → correct chips.
- Per-section error isolation (a raising query yields `{"error":...}`, others still populate).
- Spend strip reads the per-kind daily counters + last-known Rentometer value.
- `set_bland_status` stamps `bland_triggered_at` when the column is known, and **omits it** (still writing `bland_call_id`) when `_lead_contact_known_columns()` doesn't list it — guarding the pre-migration window.

(The HTML is rendered/validated manually via the `/ops` route; no browser unit tests.)

## File structure

| File | Responsibility |
|---|---|
| `services/ops_stats.py` (new) | all section aggregations + health flags |
| `dashboard/ops.html` (new) | render `/api/ops` JSON, dark theme, Refresh |
| `dashboard/main.py` (modify) | `GET /ops` + `GET /api/ops` (auth) |
| `services/enrichment_cache.py` (modify) | tiny `ops_kv` get/set for last-known Rentometer credits |
| `scripts/backfill_rent.py` / rent call path (modify) | record `credits_remaining` after Rentometer calls |
| `migrations/0XX_lead_contacts_bland_triggered_at.sql` (new) | additive nullable `bland_triggered_at TIMESTAMPTZ` for the fired/day trend |
| `services/dedup_service.py` (modify) | `set_bland_status` stamps `bland_triggered_at` when a fire dispatches |
| `tests/test_ops_stats.py` (new) | unit tests for aggregation + flags |

**Operator action:** apply `migrations/0XX_lead_contacts_bland_triggered_at.sql` to the live DB (additive nullable column — safe; writes degrade gracefully until applied, so deploy order doesn't matter).

## Out of scope (YAGNI)

- Per-county funnel (only per-track funnel; per-county is scrapes-only).
- Auto-refresh / wall-display mode.
- Charting library — sparklines are inline unicode/CSS, no JS chart dep.
- Historical storage beyond what `run_metrics` already keeps (7-day trend reads existing rows).
