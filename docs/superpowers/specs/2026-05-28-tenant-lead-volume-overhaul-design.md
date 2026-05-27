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

This spec is a **unified overhaul** delivered in five phases (0, 1, 1.5, 2, 3). Phase 3 is scoped here but not built in this spec.

### Out of scope
- Rent threshold values stay at current per-state numbers.
- BatchData property skip-trace (already removed for NG; landlord track unchanged).
- GHL pipeline definitions (only stage routing changes).
- Yellow-source path (`enrich_tenant_by_name`).
- Tarrant scraper revival (0 filings in production today; separate issue).
- GA scraper revival (7 filings lifetime; separate issue).

## Goals

1. Stop discarding 89% of TX (and 41% of TN, 95% of AZ) filings via a target-mismatched ZIP gate.
2. Preserve current Vantage lead flow (no revenue disruption during the transition).
3. Capture the previously-discarded universe so Phase 3 can make data-driven promote/enrich decisions, with a Phase 1.5 manual-promotion lever available immediately for volume-starved cohorts.
4. Codify the `select-searchbug-tenant-leads` skill's 9-gate filter as the runtime enrichment policy.
5. Fix scraper-level name-hygiene bugs that contaminate ~5% of in-window leads.
6. Reclassify the 1,719 unclassified Franklin filings sitting in Supabase.
7. Diagnose and land a fix (or named escalation) for OH's 0/66 phone yield.
8. Eliminate the BatchData property-lookup call in tenant-only mode.
9. **Remove DNC scrubbing entirely** as a deliberate policy choice (see Risk section for legal note).
10. Introduce a **verified-lead taxonomy** with auto-push, review-stage, and drop lanes so ambiguous and name-mismatch SearchBug results land in a human review queue instead of being dropped.

## Non-goals

- A fully automated Phase 3 promote-and-enrich policy (separate spec).
- Migration of historical `discard_reason = 'zip_not_approved'` rows (left as historical).
- Restructuring `qualification.py` into composable stages.

---

## Verified-Lead Taxonomy

Every filing that reaches enrichment lands in one of three lanes after SearchBug responds:

| SearchBug status | Lane | Routing |
|---|---|---|
| `phone_found` (single result, name matches) | **Auto** | GHL standard NG pipeline (`GHL_NG_RESIDENTIAL_STAGE_ID` or `GHL_NG_COMMERCIAL_STAGE_ID`) + Instantly enrollment + Bland queue |
| `name_mismatch` (single result, different person) | **Review** | Extract the phone. Push to new `GHL_NG_REVIEW_STAGE_ID` with custom fields `expected_name`, `returned_name`. **No Instantly. No Bland.** |
| `ambiguous` (multiple results) | **Review** | Push the filing to `GHL_NG_REVIEW_STAGE_ID` without a phone, tagged `Ambiguous-Lookup`. **No Instantly. No Bland.** |
| `no_records` / `no_person` / `no_phone` / errors | **Drop** | Current behavior unchanged. No GHL contact created. |

"Verified" = the Auto lane only. Bland/Instantly never fire for Review or Drop.

---

## Design

### Phase 0 — Name hygiene at the scraper boundary

**Why first**: the retrofitted 9-gate filter (Phase 2) and the verified-lead taxonomy rely on `parse_name` returning clean output. Production data shows ~5% of in-window rows carry contamination that defeats SearchBug matching even when the row is otherwise enrichable.

**Changes:**

- New shared helper `clean_tenant_name(raw: str) -> str` in [services/name_utils.py](services/name_utils.py). Handles all observed patterns:
  - Trailing-period variants (`"…and all other occupants."`).
  - `"and/or All Occupants"` patterns.
  - `"of [address]"` suffix that contaminates the name with the property address.
  - Multi-token noise tails (`"AND ALL OCCUPANTS, UNKNOWN OCCUPANTS, TENANTS, AND SUBTENANTS"`).
  - Returns empty string for placeholders (`"John Doe"`, `"Jane Doe"`, `"All Occupants"`, `"Unknown Tenant"`, `"Squaters"`).
- `parse_name()` updated to keep compound-surname particles (`De La`, `De Los`, `Van Der`, `Del`, `Da`) in the last-name slot rather than dropping them. Edge case tests must include both `"Stephanie De Los Santos"` (particle as last-name) and `"John De Smith"` (particle as middle).
- Harris and Tarrant scrapers replace their local `_clean_defendant`/`_clean_tenant` with `clean_tenant_name()`. Other scrapers gain the call before constructing `Filing`.
- Broader business regex in [pipeline/runner.py](pipeline/runner.py) — `_BUSINESS_RE` adds `ESTATE OF`, `DBA`, `C/O`, `S\.A\.`, `BANK`.
- New heuristic `infer_property_type(filing) -> str` in `services/name_utils.py`:
  - `commercial` if `notice_type` matches `commercial|retail|office`.
  - `commercial` if `tenant_name` matches the broadened business regex.
  - `residential` otherwise.
- [pipeline/runner.py](pipeline/runner.py) tenant-only branch stops calling `batchdata_service.lookup_property_info`. EC branch keeps it.

**Tests**:
- `test_name_utils.py`: 1 case per leak pattern, sourced from actual production strings — `"Kenae Mayhorn and all other occupants."`, `"Brenda V Villarreal and/or All Occupants"`, `"Dana Breyuntae Knighten and/or All Occupants of 3119 Peachstone Pl Spring, TX 7389-4688"`, `"Stephanie De Los Santos"`, `"John De Smith"`, `"John Doe"`.
- `test_runner.py`: assert no BatchData calls happen in tenant-only mode after the lookup removal.

### Phase 1 — Capture mode for the expanded ZIP universe

**Behavior:**
- New env var `CAPTURE_EXPANDED_ZIPS` (default `true` once shipped).
- When `true`: filings whose ZIP is **not** on the legacy `APPROVED_ZIPS` allowlist are stored with `lead_bucket = 'captured'` and skip enrichment + routing + GHL + Bland + Instantly entirely.
- When `false`: behavior reverts exactly to today's (`zip_not_approved` discard). One-flag revert path.
- Filings whose ZIP **is** on the allowlist follow the existing path unchanged.

**Code changes:**
- [pipeline/qualification.py](pipeline/qualification.py): `classify_lead()` gains `capture_expanded: bool` parameter. New return outcome `captured`. `APPROVED_ZIPS` stays with header comment documenting its EC-era origin.
- [pipeline/runner.py](pipeline/runner.py): read flag at module load. Pass to `_classify_and_store`. After classification, if `lead_bucket == 'captured'`: log, increment new `captured` run metric, `continue`.
- New run-metric counters: `captured` (overall and per-state), surfaced in the run summary.
- [dashboard/main.py](dashboard/main.py): new "Captured (expanded ZIPs)" read-only view.

**Schema:**
- No migration. `lead_bucket TEXT` accepts `captured` as a new value.

**Phase 1 success criterion**: after 14 days of production capture mode, `lead_bucket='captured'` count ≥ 3,000 with state distribution roughly matching projection. This triggers the Phase 3 brainstorm.

**Tests:**
- `test_qualification.py`: `capture_expanded=true` + off-allowlist ZIP → `captured`. `capture_expanded=false` + off-allowlist ZIP → `zip_not_approved` (regression guard). On-allowlist ZIP unaffected either way.
- `test_runner.py`: with `CAPTURE_EXPANDED_ZIPS=true`, an off-allowlist filing reaches the DB as `captured` with no BatchData / SearchBug / GHL / Bland / Instantly calls.

### Phase 1.5 — Manual promote-by-ZIP cohort script

**Purpose:** Vantage is volume-starved today. Phase 1 captures inventory but enriches none of it. Phase 1.5 gives a hand-controlled lever to pull captured cohorts into the enrichment funnel without waiting for Phase 3's automated policy.

**Tool:** `scripts/promote_captured_zips.py`

```
python scripts/promote_captured_zips.py --state TX --zips 77090,77042,77077 --since 2026-05-01
python scripts/promote_captured_zips.py --state TX --zips 77090 --demote   # revert
python scripts/promote_captured_zips.py --state TX --zips 77090 --dry-run  # cost projection
```

**Behavior:**
- Reads `captured` rows matching `(state, zips, filing_date >= since)`.
- For each: sets `lead_bucket='residential_approved'`, appends to `qualification_notes` (`"Promoted from captured by ZIP cohort YYYY-MM-DD"`), refreshes `classified_at`.
- Next scheduled runner cycle picks them up and runs through the 9-gate filter + SearchBug + verified-lead taxonomy.
- `--dry-run` reports projected SearchBug call volume + approximate cost.
- `--demote` reverses (sets `lead_bucket` back to `captured`, appends `qualification_notes` demotion entry).

**Tests:**
- `test_promote_captured_zips.py`: dry-run produces expected count; promotion is idempotent; demote reverses cleanly.

### Phase 2 — 9-gate retrofit, DNC removal, review-stage, backlog, OH yield

Five parallel pieces shipping together.

#### 2.1 — 9-gate retrofit

`pipeline/runner.py` enrichment branch picks up the [`select-searchbug-tenant-leads`](C:/Users/Zeann/.claude/skills/select-searchbug-tenant-leads) skill's gates before any paid call:

| Gate | Implementation | Run metric on fail |
|---|---|---|
| `lead_bucket = 'residential_approved'` | Already implicit | (none — implied) |
| `filing_date` within window (default 10 days) | New env `ENRICHMENT_WINDOW_DAYS=10` | `gate_out_of_window` |
| `court_date IS NULL OR court_date >= today` | Inline check | `gate_overdue` |
| `property_address` valid (street # + city + ST + ZIP) | Regex enforcement | `gate_invalid_address` |
| `tenant_name` clean + parses | Phase 0 `clean_tenant_name` + `parse_name` | `gate_bad_name` |
| No existing tenant phone in `lead_contacts` (track=ng) | New `lead_contacts` lookup pre-SearchBug | `gate_existing_phone` |
| Surname passes `is_common_surname()` | Existing behavior preserved | `gate_surname` |
| Normalized address-qualified query unique within run | In-memory dedup set keyed by `(first, last, query_street_address, zip)` | `gate_duplicate_in_run` |

Each failed gate increments a run metric so telemetry shows where leads are dropping.

**Address-regex regression budget**: before shipping, a one-time audit pass counts how many currently-approved historical rows would fail the stricter address regex. If >5% of approved rows would fail, the regex relaxes; otherwise it ships.

#### 2.2 — DNC removal

DNC scrubbing is removed entirely. **This is a deliberate policy decision by the project owner.**

> **Legal-exposure note**: removing DNC scrubbing increases TCPA / FTC exposure. Federal penalties are $500–$1,500 per violation (per outbound call or text to a listed number). State attorneys general have been active in eviction-defense and debt-relief space. This spec captures the removal as an explicit choice; the rationale (volume constraints, fail-closed behavior on `unknown`) is the owner's call.

**Changes:**
- Remove `dnc_service` import and all call sites in [pipeline/runner.py](pipeline/runner.py).
- Remove `_apply_ftc_scrub()` and the `dnc_decision = dnc_service.can_call(contact)` gate.
- Phone contacts no longer check DNC status before GHL push.
- DNC columns in Supabase (`dnc_status`, `dnc_source`, `dnc_checked_at`, `ng_dnc_status`, etc.) stay (no migration); they're simply no longer written.
- `bland_status` values `blocked_dnc` / `pending_dnc_review` are no longer emitted.
- Keep the Pushover error infrastructure; remove only DNC-specific alerts.

#### 2.3 — Review-stage routing for ambiguous + name_mismatch

**Code changes:**
- [services/searchbug_service.py](services/searchbug_service.py): `search_tenant_detailed` now extracts the phone for `name_mismatch` (single-result) responses and returns it on the `SearchBugResult`. The `status` field still says `name_mismatch` so the caller knows to route differently. `ambiguous` still returns no phone (no single answer).
- [pipeline/runner.py](pipeline/runner.py): when SearchBug status is `name_mismatch` or `ambiguous` AND the basic gates passed:
  - If `name_mismatch` and phone present: create `EnrichedContact` with the phone, push to `GHL_NG_REVIEW_STAGE_ID` with custom fields `expected_name` + `returned_name`. Tag `Name-Mismatch-Review`. Skip Bland trigger. Skip Instantly enrollment.
  - If `ambiguous`: create `EnrichedContact` without a phone, push to `GHL_NG_REVIEW_STAGE_ID`. Tag `Ambiguous-Lookup`. Skip Bland. Skip Instantly.
- New env var: `GHL_NG_REVIEW_STAGE_ID`.
- New run-metric counters: `ng_review_name_mismatch`, `ng_review_ambiguous`.

#### 2.4 — Franklin backlog reclassification

`scripts/reclassify_franklin_backlog.py`:
- Pulls Supabase `filings` where `state='OH'` and `lead_bucket IS NULL` (covers 1,719 Franklin + 3 Hamilton + any future OH null rows).
- For each row: re-derive `property_zip` from `property_address`, run through `classify_lead(capture_expanded=True)`, write back `lead_bucket` / `discard_reason` / `qualification_notes` / `classified_at`.
- Idempotent (`WHERE classified_at IS NULL` guard).
- `--dry-run` flag prints projected bucket distribution without writing.
- Expected outcome: nearly all 1,719 land as `captured` (most Franklin ZIPs are outside the OH allowlist).

#### 2.5 — OH SearchBug diagnosis and fix

`scripts/diagnose_oh_searchbug.py`:
- Runs three known-good Cincinnati and three known-good Columbus filings through `search_tenant_detailed` with verbose logging.
- Captures request payload, response status, rows count, name-match outcome.
- Compares yellow-path (no ADDRESS) vs green-path (with ADDRESS) calls.

**Decision branch (in spec, not deferred):**
- If diagnosis identifies a fixable root cause (credentials, ZIP narrowing, city normalization, payload bug) → fix ships in this same Phase 2 commit. Diagnosis note saved to `docs/superpowers/specs/notes/oh-searchbug-diagnosis.md`.
- If diagnosis identifies a non-fixable cause (SearchBug coverage gap, account error needing top-up) → escalate via Pushover with two named follow-up options: (a) test Enformion as alternate provider for OH, (b) defer OH enrichment to Phase 3 reroute. Either path becomes its own spec.

#### Tests:
- `test_runner_gates.py` (new): one test per gate, asserting the run-metric counter increments and no SearchBug call happens.
- `test_runner.py`: verified-lead → auto-push (GHL + Instantly + Bland); name_mismatch → review-stage push only; ambiguous → review-stage push only.
- `test_searchbug_service.py`: assert `name_mismatch` response now includes the extracted phone.
- `test_reclassify_franklin_backlog.py`: dry-run prints expected distribution; second invocation is a no-op.

### Phase 3 — Promote-and-enrich automated (scoped here, built later)

**Not built in this spec.** Phase 1.5's manual promotion is the bridge.

Phase 3 will:
- Analyze captured rows (per-ZIP filing volume, demographic clustering, observed rent distribution where available, eviction-volume signals).
- Promote selected captured cohorts from `lead_bucket = 'captured'` → `'residential_approved'` automatically based on cohort-level signals (not per-row hand selection).
- Track bucket transitions in an append-only `bucket_history` audit (small new table or column, defined in the Phase 3 spec).
- Re-enrich the promoted rows through the same 9-gate filter and verified-lead taxonomy.

Phase 1 does **not** create `bucket_history`. The initial bucket assignment is recorded by `classified_at` on the filings row; Phase 3 will backfill `bucket_history` entries from `classified_at` when it ships.

---

## Expected outcomes

Per-phase projection of incremental NG GHL contacts per month, against the current ~30/mo run rate (extrapolated from 194 lifetime).

| Phase | Direct lead impact | Notes |
|---|---|---|
| Phase 0 (name hygiene) | +5–8 contacts/mo | Recovers leads currently failing SearchBug due to name contamination |
| Phase 1 (capture mode) | 0 | Captures data only; no enrichment |
| Phase 1.5 (manual promote 3 TX ZIPs) | +20–30 contacts/mo | Hand-pick top discarded TX ZIPs (e.g., 77090 + 77042 + 77077). Volume scales with how many ZIPs are flipped on |
| Phase 2.1 (9-gate) | 0 new; -$50–100/mo SearchBug spend | Efficiency. Frees cap headroom for promoted cohorts |
| Phase 2.2 (DNC removal) | +3–8 contacts/mo | Recovers leads previously DNC-blocked or fail-closed on `unknown` |
| Phase 2.3 (review-stage) | +10–15 contacts/mo entering Review | Lane shift, not Auto-lane growth. Human-followed leads that previously dropped |
| Phase 2.4 (Franklin backlog) | +5 contacts/mo (depends on OH yield) | Most rows land as `captured`; some land approved if ZIP-matched. Requires Phase 2.5 fix to convert |
| Phase 2.5 (OH SearchBug fix) | +15–25 contacts/mo if fixable | Lifts OH from 0% phone-hit to ~20% (matching TN) |
| Phase 2 (BatchData drop) | 0 | $18/mo saved |
| **Phase 0–2 total (this spec)** | **+55 to +85 Auto-lane contacts/mo** | Plus +10–15/mo Review-lane |
| Phase 3 (separate) | +150–250 contacts/mo projected | Where the real volume lift lives |

**Captured-table accumulation** (Phase 1 + Phase 2.4 backfill):

| Time horizon | Pessimistic (Franklin patchy) | Optimistic (Franklin steady) |
|---|---:|---:|
| Day 0 (post-backfill) | ~1,700 | ~1,700 |
| Day 14 | ~3,800 | ~4,600 |
| Day 30 | ~6,200 | ~8,000 |
| Day 90 | ~15,000 | ~20,000 |

Phase 3 brainstorm should start no later than Day 30.

---

## Risk and rollback

- **Capture mode is one flag.** `CAPTURE_EXPANDED_ZIPS=false` reverts to legacy ZIP-discard behavior immediately.
- **9-gate retrofit is purely additive** in the rejection direction.
- **DNC removal increases TCPA / FTC exposure.** See note in Phase 2.2. This is a deliberate policy choice; the spec captures it explicitly so future maintainers don't treat it as an oversight.
- **Phase 1.5 promote-by-ZIP** can over-enrich if the operator picks high-volume ZIPs without budget awareness. `--dry-run` mitigates; daily cap still applies as a backstop.
- **Phase 0 name hygiene** is shared-helper extraction with regex broadening. Worst case: an over-aggressive trailer regex strips legitimate name suffix. Tests use real production strings to bound that risk.
- **Franklin backlog script** is idempotent and dry-runnable.
- **Address-regex stricter check in Phase 2.1** could reject currently-approved rows. Audit pass before shipping; if >5% would fail, relax the regex.

## Observability

- New `captured` run-metric per state and total, in the run summary.
- New `gate_*` counters per skipped category. Sum should equal "enrichment attempted - phones found - misses".
- New `ng_review_name_mismatch` and `ng_review_ambiguous` counters track review-lane volume.
- Dashboard "Captured" view exposes raw captured inventory for human inspection.
- No new alerts in this spec; existing Pushover paths (cap hit, account error) unchanged. DNC alerts removed.

## Files touched

| File | Change |
|---|---|
| `services/name_utils.py` | New `clean_tenant_name`, compound-particle support in `parse_name`, new `infer_property_type` helper |
| `services/searchbug_service.py` | `name_mismatch` response now extracts and returns the phone |
| `services/dnc_service.py` | Deleted or its usage removed; module may stay for any landlord-track residual |
| `pipeline/qualification.py` | `classify_lead(capture_expanded)` param, `captured` outcome, legacy comment on `APPROVED_ZIPS` |
| `pipeline/runner.py` | Read `CAPTURE_EXPANDED_ZIPS`, capture-mode short-circuit, 9-gate retrofit, remove tenant-only `lookup_property_info` call, broadened `_BUSINESS_RE`, **DNC removal**, **review-stage routing** for `name_mismatch` / `ambiguous`, skip Instantly + Bland for Review lane |
| `pipeline/router.py` | (verified) no change |
| `dashboard/main.py` | "Captured (expanded ZIPs)" read-only view |
| `scrapers/texas/harris.py` | Call shared `clean_tenant_name` |
| `scrapers/texas/tarrant.py` | Call shared `clean_tenant_name` |
| `scrapers/*/*.py` (other green scrapers) | Call shared `clean_tenant_name` before `Filing` construction |
| `scripts/reclassify_franklin_backlog.py` | New |
| `scripts/diagnose_oh_searchbug.py` | New (with branched fix-or-escalate path) |
| `scripts/promote_captured_zips.py` | New (Phase 1.5) |
| `tests/test_name_utils.py` | Cases from production-observed leak patterns + compound-particle edges |
| `tests/test_qualification.py` | Capture-mode cases |
| `tests/test_runner.py` | Capture short-circuit, 9-gate assertions, verified-lead lanes, DNC removal regression |
| `tests/test_runner_gates.py` | New, one test per gate |
| `tests/test_searchbug_service.py` | Updated for name_mismatch phone extraction |
| `tests/test_reclassify_franklin_backlog.py` | New |
| `tests/test_promote_captured_zips.py` | New |
| `tests/test_dnc_service.py` | Deleted (or trimmed to landlord-only if anything survives) |
| Env / Railway | New: `CAPTURE_EXPANDED_ZIPS`, `ENRICHMENT_WINDOW_DAYS`, `GHL_NG_REVIEW_STAGE_ID` |
