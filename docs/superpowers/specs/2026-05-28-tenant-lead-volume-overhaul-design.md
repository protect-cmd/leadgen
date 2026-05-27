# Tenant Lead Volume Overhaul — Design

**Date:** 2026-05-28
**Status:** Design (pending implementation)
**Spec owner:** Zee
**Track affected:** NG (Vantage Defense / tenant)

---

## Problem

Tenant lead output is far below what the scraped data could support. Across 7,442 lifetime filings, only ~194 NG GHL contacts have been created (~2.6%). The Harris (TX) funnel illustrates the leak:

```
4,489 raw filings
   -3 missing_zip (scraper didn't yield ZIP)
-4,002 ZIP filter (89.2% — single largest cut)
  484 pass qualification
 -213 rent_below_threshold
  -14 held (>7 days)
  271 enrichment attempted
 -178 returned neither phone nor email (66% silent failure)
   93 have phone or email
   14 actually pushed as NG GHL contacts (0.31% of raw)
```

Two compounding leaks dominate the loss: **the ZIP filter** (loud, gates 89% of TX volume upfront) and **enrichment yield** (quiet, drops 5% of qualified rows into reachable tenant leads). This spec addresses both, plus several smaller data-quality and policy issues uncovered during the audit.

## Why the ZIP filter looks the way it does

The current `APPROVED_ZIPS` allowlist was built for the landlord (EC / Grant Ellis) side and never recalibrated when the tenant (NG / Vantage Defense) track was added. The list contents make the origin obvious:

- **TX**: River Oaks, Highland Park, Park Cities, Tarrytown, West Lake Hills, downtown cores.
- **GA**: Buckhead, Sandy Springs.
- **FL**: Fisher Island, Brickell, Coral Gables.

These are affluent urban areas — *where a property owner is a high-value prospect*. They are not where eviction filings concentrate. The top discarded TX ZIPs (77090 Greenspoint, 77042 Westchase, 77070 Cypress, 77084 Bear Creek) are dense apartment-heavy middle-class suburbs — exactly Vantage Defense's target tenant demographic.

The demographics for tenant defense are nearly opposite the demographics for landlord prospecting:

| | Landlord side (EC) | Tenant side (NG) |
|---|---|---|
| Wants | High property value, affluent owners | Renter density, eviction volume |
| ZIP shape | Urban core, wealthy suburbs | Apartment-heavy outer ring |

No feedback loop existed to surface this misalignment — phone-hit rate per ZIP cohort was never compared to the allowlist, so the 89% TX kill rate compounded silently.

## Scope

This spec is a **unified overhaul** delivered in four phases. Phase 3 is scoped here but not built in this spec.

### Out of scope
- Rent threshold values stay at current per-state numbers.
- BatchData property skip-trace (already removed for NG; landlord track unchanged).
- GHL / Bland / Instantly routing.
- Yellow-source path (`enrich_tenant_by_name`).
- Tarrant scraper revival (0 filings in production today; separate issue).
- GA scraper revival (7 filings lifetime; separate issue).

## Goals

1. Stop discarding 89% of TX (and 41% of TN, 95% of AZ) filings via a target-mismatched ZIP gate.
2. Preserve current Vantage lead flow (no revenue disruption during the transition).
3. Capture the previously-discarded universe so Phase 3 can make data-driven promote/enrich decisions.
4. Codify the `select-searchbug-tenant-leads` skill's 9-gate filter as the runtime enrichment policy, eliminating wasted SearchBug calls on overdue cases, already-phoned leads, and duplicate queries.
5. Fix scraper-level name-hygiene bugs that contaminate ~5% of in-window leads (trailer regex misses, placeholders, compound surnames).
6. Reclassify the 1,719 unclassified Franklin filings sitting in Supabase.
7. Diagnose why OH has produced 0 phones from 66 enrichable filings.
8. Eliminate the BatchData property-lookup call in tenant-only mode (~$18/mo, third-party dependency).

## Non-goals

- A new policy for what *should* be enriched from the captured universe (Phase 3, separate spec).
- Migration of historical `discard_reason = 'zip_not_approved'` rows (left as historical).
- Restructuring `qualification.py` into composable stages (rejected — refactor without immediate value).

---

## Design

### Phase 0 — Name hygiene at the scraper boundary

**Why it's first**: the retrofitted 9-gate filter (Phase 2) relies on `parse_name` returning clean output. Production data shows ~5% of in-window rows carry contamination that defeats SearchBug matching even when the row is otherwise enrichable.

**Changes:**

- New shared helper `clean_tenant_name(raw: str) -> str` in [services/name_utils.py](services/name_utils.py). Handles all observed patterns:
  - Trailing-period variants (`"…and all other occupants."`).
  - `"and/or All Occupants"` patterns.
  - `"of [address]"` suffix that contaminates the name with the property address.
  - Multi-token noise tails (`"AND ALL OCCUPANTS, UNKNOWN OCCUPANTS, TENANTS, AND SUBTENANTS"`).
  - Returns empty string for placeholders (`"John Doe"`, `"Jane Doe"`, `"All Occupants"`, `"Unknown Tenant"`, `"Squaters"`), causing downstream `bad_name` gate to reject.
- `parse_name()` updated to keep compound-surname particles (`De La`, `De Los`, `Van Der`, `Del`, `Da`) in the last-name slot rather than dropping them.
- Harris and Tarrant scrapers replace their local `_clean_defendant`/`_clean_tenant` with `clean_tenant_name()`. Other scrapers gain the call before constructing `Filing`.
- Broader business regex in [pipeline/runner.py](pipeline/runner.py) — `_BUSINESS_RE` adds `ESTATE OF`, `DBA`, `C/O`, `S\.A\.`, `BANK`.
- New heuristic `infer_property_type(filing) -> str` in `services/name_utils.py`:
  - `commercial` if `notice_type` matches `commercial|retail|office`.
  - `commercial` if `tenant_name` matches the broadened business regex.
  - `residential` otherwise.
- [pipeline/runner.py](pipeline/runner.py) tenant-only branch stops calling `batchdata_service.lookup_property_info`. EC branch keeps it.

**Tests**:
- `test_name_utils.py`: 1 case per leak pattern, sourced from actual production strings (not synthetic) — `"Kenae Mayhorn and all other occupants."`, `"Brenda V Villarreal and/or All Occupants"`, `"Dana Breyuntae Knighten and/or All Occupants of 3119 Peachstone Pl Spring, TX 7389-4688"`, `"Stephanie De Los Santos"`, `"John Doe"`, etc.
- `test_runner.py`: assert no BatchData calls happen in tenant-only mode after the lookup removal.

### Phase 1 — Capture mode for the expanded ZIP universe

**Behavior:**
- New env var `CAPTURE_EXPANDED_ZIPS` (default `true` once shipped).
- When `true`: filings whose ZIP is **not** on the legacy `APPROVED_ZIPS` allowlist are stored with `lead_bucket = 'captured'` and skip enrichment + routing + GHL + Bland + Instantly entirely.
- When `false`: behavior reverts exactly to today's (`zip_not_approved` discard). One-flag revert path.
- Filings whose ZIP **is** on the allowlist follow the existing path unchanged (`residential_approved` / `held` / `commercial` / `rent_below_threshold`). Vantage's lead flow is not affected.
- `discard_reason = 'zip_not_approved'` is no longer written for new rows. Historical rows keep the value.

**Code changes:**
- [pipeline/qualification.py](pipeline/qualification.py): `classify_lead()` gains a `capture_expanded: bool` parameter. New return outcome `captured` with appropriate notes. `APPROVED_ZIPS` stays in file with a header comment documenting its EC-era origin and the rationale for the `captured` bucket.
- [pipeline/runner.py](pipeline/runner.py): read flag at module load. Pass to `_classify_and_store`. After classification, if `lead_bucket == 'captured'`: log, increment new `captured` run metric, and `continue` (skip enrichment + routing).
- New run-metric counters: `captured` (overall and per-state), surfaced in the run summary.
- [dashboard/main.py](dashboard/main.py): new "Captured (expanded ZIPs)" read-only view. Browseable by state, ZIP, county. No actions wired (Phase 3 territory).

**Schema:**
- No migration. `lead_bucket` is `TEXT`; `captured` is just a new value. Downstream consumers that filter on `residential_approved` keep working unchanged.

**Tests:**
- `test_qualification.py`: `capture_expanded=true` + off-allowlist ZIP → `captured`. `capture_expanded=false` + off-allowlist ZIP → `zip_not_approved` discard (regression guard). On-allowlist ZIP unaffected either way.
- `test_runner.py`: with `CAPTURE_EXPANDED_ZIPS=true`, an off-allowlist filing reaches the DB as `captured` with no BatchData / SearchBug / GHL / Bland / Instantly calls.

### Phase 2 — 9-gate retrofit + backlog + OH yield

Two parallel pieces shipping together.

**9-gate retrofit** — `pipeline/runner.py` enrichment branch picks up the [`select-searchbug-tenant-leads`](C:/Users/Zeann/.claude/skills/select-searchbug-tenant-leads) skill's gates before any paid call:

| Gate | Implementation | Run metric on fail |
|---|---|---|
| `lead_bucket = 'residential_approved'` | Already implicit | (none — implied) |
| `filing_date` within window (default 10 days) | New env `ENRICHMENT_WINDOW_DAYS=10` | `gate_out_of_window` |
| `court_date IS NULL OR court_date >= today` | Inline check | `gate_overdue` |
| `property_address` valid (street # + city + ST + ZIP) | Regex enforcement, today partially trusted | `gate_invalid_address` |
| `tenant_name` clean + parses | Phase 0 `clean_tenant_name` + `parse_name` | `gate_bad_name` |
| No existing tenant phone in `lead_contacts` (track=ng) | New `lead_contacts` lookup pre-SearchBug | `gate_existing_phone` |
| Surname passes `is_common_surname()` | Existing behavior preserved | `gate_surname` |
| Normalized address-qualified query unique within run | In-memory dedup set keyed by `(first, last, query_street_address, zip)` | `gate_duplicate_in_run` |

The "prior SearchBug artifacts in tmp/" gate from the skill stays in the manual preflight tool; the runner's existing `enrichment_cache` already handles long-running dedup.

**Backlog cleanup** — `scripts/reclassify_franklin_backlog.py`:
- Pulls Supabase `filings` where `state='OH'` and `lead_bucket IS NULL` (covers 1,719 Franklin + 3 Hamilton + any future OH null rows).
- For each row: re-derive `property_zip` from `property_address`, run through `classify_lead(capture_expanded=True)`, write back `lead_bucket` / `discard_reason` / `qualification_notes` / `classified_at`.
- Idempotent (`WHERE classified_at IS NULL` guard).
- `--dry-run` flag prints projected bucket distribution without writing.
- Expected outcome: nearly all 1,719 land as `captured` (most Franklin ZIPs are outside the OH allowlist).

**OH SearchBug diagnosis** — `scripts/diagnose_oh_searchbug.py`:
- Runs three known-good Cincinnati and three known-good Columbus filings through `search_tenant_detailed`.
- Logs request payload, response status, rows count, name-match outcome.
- Compares yellow-path (no ADDRESS) vs green-path (with ADDRESS).
- Writes a diagnosis note to `docs/superpowers/specs/notes/oh-searchbug-diagnosis.md`.
- The fix itself is a follow-up commit (likely a credential, ZIP-narrowing, or city-name normalization issue), not a new spec.

**Tests:**
- `test_runner_gates.py` (new): one test per gate, asserting the run-metric counter increments and no SearchBug call happens.
- `test_reclassify_franklin_backlog.py`: dry-run prints expected distribution; second invocation is a no-op.

### Phase 3 — Promote-and-enrich (scoped here, built later)

**Not built in this spec.** Documented here so Phase 1's data model leaves the door open.

Phase 3 will:
- Analyze captured rows (per-ZIP filing volume, demographic clustering, observed rent distribution where available, eviction-volume signals).
- Promote selected captured cohorts from `lead_bucket = 'captured'` → `'residential_approved'`.
- Track bucket transitions in an append-only `bucket_history` audit (small new table or column, defined in the Phase 3 spec).
- Re-enrich the promoted rows through the same 9-gate filter.

Phase 1 does **not** create `bucket_history`. The initial bucket assignment is recorded by `classified_at` on the filings row; Phase 3 will backfill `bucket_history` entries from `classified_at` when it ships.

---

## Risk and rollback

- **Capture mode is one flag.** `CAPTURE_EXPANDED_ZIPS=false` reverts to legacy ZIP-discard behavior immediately.
- **9-gate retrofit is purely additive** in the rejection direction — no row that the old runner enriched will be enriched-now-skipped except in the case of already-phoned (saving cost) or overdue / bad-name / dup (saving waste). No legitimate lead path narrows.
- **Phase 0 name hygiene** is shared-helper extraction with regex broadening. Worst case: an over-aggressive trailer regex strips legitimate name suffix. Tests use real production strings to bound that risk.
- **Franklin backlog script** is idempotent and dry-runnable. Worst case: bad classification on 1,719 rows; re-running the script with a corrected classifier fixes them.

## Observability

- New `captured` run-metric per state and total, in the run summary.
- New `gate_*` counters per skipped category. Sum should equal "enrichment attempted - phones found - misses".
- Dashboard "Captured" view exposes the raw captured inventory for human inspection.
- No new alerts in this spec; existing Pushover paths (cap hit, account error) are unchanged.

## Files touched

| File | Change |
|---|---|
| `services/name_utils.py` | New `clean_tenant_name`, compound-particle support in `parse_name`, new `infer_property_type` helper |
| `pipeline/qualification.py` | `classify_lead(capture_expanded)` param, `captured` outcome, legacy comment on `APPROVED_ZIPS` |
| `pipeline/runner.py` | Read `CAPTURE_EXPANDED_ZIPS`, capture-mode short-circuit, 9-gate retrofit, remove tenant-only `lookup_property_info` call, broadened `_BUSINESS_RE` |
| `pipeline/router.py` | (verified) no change |
| `dashboard/main.py` | "Captured (expanded ZIPs)" read-only view |
| `scrapers/texas/harris.py` | Call shared `clean_tenant_name` |
| `scrapers/texas/tarrant.py` | Call shared `clean_tenant_name` |
| `scrapers/*/*.py` (other green scrapers) | Call shared `clean_tenant_name` before `Filing` construction |
| `scripts/reclassify_franklin_backlog.py` | New |
| `scripts/diagnose_oh_searchbug.py` | New |
| `tests/test_name_utils.py` | New cases from production-observed leak patterns |
| `tests/test_qualification.py` | Capture-mode cases |
| `tests/test_runner.py` | Capture short-circuit + 9-gate assertions |
| `tests/test_runner_gates.py` | New, one test per gate |
| `tests/test_reclassify_franklin_backlog.py` | New |
