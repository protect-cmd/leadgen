# Pipeline Audit — 2026-06-12

Read-only review of the lead system: scrape → qualify → flag → score → enrich → DNC → fire,
plus `/lists` (To-Enrich / To-Fire), the v2 scoring, and the business logic. Findings are
ordered by severity with `file:line` evidence. Items marked **[fixing]** are addressed in the
companion PR.

## 🔴 Critical

### C1. Vantage fire path has no calling-hours gate (TCPA exposure) **[fixing]**
`services/bland_service.py` `trigger_voicemail` enforces DNC but performs **no time-window
check**. The ISTS path does — `services/ists_bland.py` `_in_call_window` (9am–6pm CT, no Sunday
before 10am). So Vantage "Fire selected" (and any scripted/automated fire) can dial **outside
legal calling hours**. Today we stayed compliant only because the operator checked the clock
manually before firing 61 leads.

**Fix:** new `services/call_window.py` — per-state local timezone, TCPA-safe 8am–9pm default
(env-tunable `CALL_WINDOW_START_HOUR`/`END_HOUR`), no Sunday before 10am. Gate `fire_case`
(clean `outside_window` status) and `trigger_voicemail` (backstop for the EC/approve callers).

### C2. Cross-track dedup ("ISTS wins") silently no-ops in To-Enrich **[fixing]**
`pipeline/queue_builder.py` `_SELECT` omits `property_zip`, but `_suppress_ists` keys on
`r.get("property_zip")` → always `None` for Vantage rows → never matches the ISTS person-keys
(which use the real ZIP). Suppression therefore **never fires** in `build_to_enrich`. A tenant
who is both an ISTS judgment and a Vantage filing gets enriched + dialed on **both tracks**
(double SearchBug + double Bland to the same person). It works in `build_to_fire` only because
that path selects `property_zip`.

**Fix:** add `property_zip` to `_SELECT`.

## 🟠 High

### H1. Automation stops at scraping
`services/daily_scheduler.py` schedules scrapers only. Not scheduled: `flag_enrichable`,
`normalize_court_date`, rent backfill, the ISTS scrape (`run_ists_harris`), enrich, fire. The
back half of the pipeline is ~4 manual scripts every morning. Live consequence today: scraped
leads sat at `is_enrichable=NULL` (invisible to `/lists`), rents null (scoring starved), ISTS
list empty, Davidson `court_date = filing_date` sentinels.

**Fix (proposed):** daily post-scrape chain `flag_enrichable → normalize_court_date →
backfill_rent`; add `run_ists_harris` to the scheduler.

### H2. Unordered pagination → non-deterministic queues **[fixing]**
`build_to_enrich` (and the other builders) paginate with `.range()` and **no `.order()`**.
PostgREST pagination without ORDER BY returns overlapping/missing rows — observed the `≥$1600`
count flip between 144 and 100 for identical data.

**Fix:** stable `.order("case_number")` before every `.range()` loop.

## 🟡 Medium

- **M1. Rent (50% of score) is null by default.** `rent_estimate_service` is gated behind
  `RENT_PRECHECK_ENABLED` and `backfill_rent` is manual, so fresh high-value leads score low
  purely from a missing input and may never get selected for estimation (self-reinforcing).
- **M2. ISTS and Vantage share one 0–100 scale, but ISTS is structurally capped** (rent floor +
  judgments always ≥3 days old → limited freshness). Blended ranking is apples-to-oranges; ISTS
  always loses. Needs an ISTS-specific score or per-track quotas. *(Product decision — not in
  this PR.)*
- **M3. ISTS enrich (7d) vs fire (14d) window mismatch** — judgments 8–14d old are fireable but
  never enrichable via the queue, and score 0 freshness.
- **M4. DNC fail-closed + local-file coverage** — with no `DNCSCRUB_LOGIN_ID`, uncovered area
  codes resolve to `unknown` → treated DNC. Compliance-safe but revenue-leaky; confirm the
  DNCScrub API is configured on Railway.

## 🟢 Low / observability

- **L1.** Ohio/Franklin scraper writes no `run_metrics` (today: 69 rows scraped, 0 metrics row).
- **L2.** Fire cap (25) is a UI-only throttle; scripted fire overrides it freely.
- **L3.** Dual enrichment paths — scrape-time runner (BatchData/SearchBug inline) vs `/lists`
  "Enrich selected" (`queue_actions`). Overlap risks double-spend / source-of-truth ambiguity.

## What's solid

- Gates (`pipeline/gates.py`) — entity/AKA/OCCUPANT exclusion + street#+state+ZIP validation.
- DNC verdict — fail-closed default, L/F-as-callable, Reason-field authority over ResultCode.
- Fire idempotency & fault isolation — `bland_call_id` skip, per-lead try/except, DNC re-scrubbed
  at dial-time independent of stored status.
