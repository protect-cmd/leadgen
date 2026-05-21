# Master Completion Plan — May 9, 2026

**Goal:** Ship the full EC + NG pipeline to live by May 11. All 13 phases below.

**Progress key:** ✅ Done · ⏳ In progress · 🔲 Not started

---

## Phase 1 — Git Baseline Stabilization ✅

- [x] Confirm clean working branch from main
- [x] All prior commits preserved and pushed
- [x] Branch `codex/dashboard-role-aware-views` created for Phase 3 work

---

## Phase 2 — Tenant BatchData Enrichment ✅

**Goal:** Stop BatchData from returning owner/LLC phone numbers instead of tenant phones.

- [x] Add `_tenant_name_matches(expected, returned) -> bool` to `services/batchdata_service.py`
  - Strips punctuation, case-insensitive
  - Ignores suffixes: JR, SR, II, III, IV
  - Rejects company terms: LLC, INC, CORP, LP, LLP, TRUST, PROPERTIES, APARTMENTS, MANAGEMENT, HOLDINGS, GROUP, REALTY, ENTERPRISES
  - First + last name match sufficient (no strict full-name requirement)
- [x] `enrich_tenant()` validates returned person name before extracting phone/email
- [x] Add `name_matched` boolean flag used in final log line (not `phone` as proxy)
- [x] Tests: `tests/test_batchdata_tenant_enrichment.py` (5 tests — exact match, normalized case, owner rejection, LLC rejection, DNC preservation)

---

## Phase 3 — Role-Aware Dashboard ✅

**Goal:** Dashboard shows EC vs NG leads separately; QA Bland call buttons per track.

### 3.1 Backend (`dashboard/main.py`)
- [x] Add `_BLAND_TEST_RECIPIENTS = {"ec": "+18883224034", "ng": "+18882141711"}`
- [x] Add `_bland_test_calls_enabled()` and `_build_bland_test_contact(track)`
- [x] Update `/api/leads` to pass `track=track` to `get_dashboard_leads`
- [x] Add `POST /api/bland-test-calls/{track}` endpoint with 403 disabled gate

### 3.2 Frontend (`dashboard/index.html`)
- [x] Add view chips: Grant Residential, Grant Commercial, Vantage Residential, Vantage Commercial, Vantage Spanish Residential, Vantage Spanish Commercial
- [x] Add `function activeTrack()` — returns `"ec"` or `"ng"` based on active view chip
- [x] Add `async function runBlandQaCall(track)` — QA call trigger
- [x] Add Bland QA buttons in header: "Call Grant QA" / "Call Vantage QA"
- [x] Add Target column header + "Target Phone" replacing "Phone"
- [x] Update fetch to include `&track=${activeTrack()}`
- [x] Phone cell uses `lead.missing_phone_label` instead of hardcoded "NO PHONE"
- [x] colspan updated 11→12

### 3.3 Tests
- [x] `tests/test_dashboard_bland_test.py` — 3 async tests for Bland QA endpoint
- [x] `tests/test_dashboard_bland_test_ui.py` — 3 HTML structural tests
- [x] `tests/test_dashboard_views.py` — 8 tests: filter, counts, track routing, row decoration

---

## Phase 4 — Bland.ai QA ⏳

**Goal:** All 3 Bland agents created, pathway IDs set in Railway, QA calls pass.

### 4.1 Scripts ✅
- [x] `_EC_VOICEMAIL_SCRIPT` updated to V2 ("This is Alex", "visit grantellisgroup.com")
- [x] `_NG_VOICEMAIL_SCRIPT` rewritten to V2 bilingual (English + Spanish bridge)
- [x] `_NG_SPANISH_VOICEMAIL_SCRIPT` updated to V2 (shorter, no `property_address`)
- [x] `render_voicemail_script()` NG branch: `property_address` removed from `.format()`

### 4.2 Setup Doc ✅
- [x] `docs/bland_ec_setup.md` — full setup guide for all 3 agents with AI builder prompts

### 4.3 Bland Pathway Creation 🔲
- [ ] Create "Grant Ellis Group Outbound" pathway in app.bland.ai
  - Use AI builder prompt from `docs/bland_ec_setup.md` Agent 1 section
  - Verify: Static Text ON on Node 1, voice = mason/derek
  - Copy pathway ID → set Railway: `BLAND_EC_AGENT_ID=<id>` (currently: `5b217638-3ee7-4cee-9e9c-f9e40a388ffc` — verify it matches V2 script)
- [ ] Create "Vantage Defense Group English Outbound" pathway
  - Use AI builder prompt from Agent 2 section
  - Verify: Static Text ON, voice = Sarah/Emily
  - Copy pathway ID → set Railway: `BLAND_NG_AGENT_ID=<id>`
- [ ] Create "Vantage Defense Group Spanish Outbound" pathway
  - Use AI builder prompt from Agent 3 section
  - Verify: Static Text ON, voice = Isabella (native Spanish)
  - Copy pathway ID → set Railway: `BLAND_NG_SPANISH_AGENT_ID=<id>`

### 4.4 Phone Numbers 🔲
- [ ] Buy NG English outbound number in app.bland.ai → set Railway: `BLAND_NG_PHONE_NUMBER`
- [ ] Buy NG Spanish outbound number → set Railway: `BLAND_NG_SPANISH_PHONE_NUMBER`
- [ ] Confirm GEG callback number → set Railway: `BLAND_EC_CALLBACK_PHONE_NUMBER`
- [ ] Set Railway: `BLAND_NG_CALLBACK_PHONE_NUMBER`, `BLAND_NG_SPANISH_CALLBACK_PHONE_NUMBER`
- [ ] Enable Local Presence add-on in Bland (app.bland.ai → Add-ons → Local Dialing)

### 4.5 Railway Flags 🔲
- [ ] Set `BLAND_ENABLED=true`
- [ ] Set `BLAND_TEST_CALLS_ENABLED=true`
- [ ] Confirm `AUTO_BLAND_CALLS_ENABLED` is NOT set (default off)

### 4.6 QA Calls (requires Zee/Chris approval) 🔲
- [ ] EC QA call to internal number — verify checklist in `docs/bland_ec_setup.md`
- [ ] NG English QA call — verify bilingual script plays correctly
- [ ] NG Spanish QA call — verify full Spanish delivery

---

## Phase 5 — GHL Verification 🔲

**Goal:** GHL contacts route to correct EC vs NG subaccounts with correct pipeline stages.

- [ ] Set Railway: `GHL_EC_REVIEW_STAGE_ID=<id>`
- [ ] Set Railway: `GHL_WEBHOOK_SECRET=<secret>`
- [ ] Verify EC filing → GHL EC subaccount contact created with "New Filing" stage
- [ ] Verify NG filing → GHL NG subaccount contact created (subaccount may not exist yet — see memory: GHL subaccounts)
- [ ] Verify opportunity alerts fire correctly (not noisy — fix committed May 9)

---

## Phase 6 — Rent Filter 🔲

**Goal:** Skip residential leads below rent threshold before BatchData call (cost savings).

- [ ] Confirm current threshold in `pipeline/router.py` ($1,800 residential minimum)
- [ ] Add pre-enrichment rent check in `pipeline/runner.py` to skip BatchData call if estimated rent is below threshold at address lookup stage
- [ ] Tests covering skip-before-batchdata path

---

## Phase 7 — Florida Scrapers ✅ (scrapers done, GA pending)

**Goal:** FL eviction filings scraped daily; GA Fulton verified live.

### 7.1 Florida ✅
- [x] `scrapers/florida/miami_dade.py` — Playwright, returns `[]` defensively
- [x] `scrapers/florida/broward.py` — Playwright, returns `[]` defensively
- [x] `scrapers/florida/hillsborough.py` — Playwright, returns `[]` defensively
- [x] `scripts/smoke_scrapers.py` — added `"florida"` factory + `fl`, `miami`, `miami-dade`, `broward`, `hillsborough` aliases
- [x] `jobs/run_florida.py` — runs all 3 scrapers in sequence
- [x] `tests/test_florida_scrapers.py` — 23 fixture-backed tests, no live calls

### 7.2 Georgia Fulton 🔲
- [ ] Run Fulton County scraper live and verify filings are returned
- [ ] Check `scrapers/south_carolina/richland.py` for any related SC fixes

---

## Phase 8 — Sunshine Notifications ✅

**Goal:** Sunshine receives daily scrape reports and job error alerts via Pushover.

- [x] `notification_service.py`: `_config()` returns `list[str]` instead of single `str`
- [x] `PUSHOVER_USER_KEYS` (comma-separated) support; falls back to `PUSHOVER_USER_KEY`
- [x] `send_alert()` loops over users, single `httpx.AsyncClient`, partial success handling
- [x] Railway: `PUSHOVER_USER_KEYS=ut7sfa79riohvam5fpirdhqpgk7e36,ui64yfwsi491y2xqaqq7aqqgdf5kyd` (Zee first, Sunshine second)
- [x] 4 new tests: multi-key, one-failure-doesn't-cancel, disabled sends nothing, all-fail returns False

---

## Phase 9 — Brand Audit ✅

**Goal:** All scripts and docs use "Grant Ellis Group" (EC) and "Vantage Defense Group" (NG). No legacy brand names.

- [x] All voicemail scripts use new brand names only
- [x] Test `test_bland_scripts_use_new_brand_names_only` asserts no EvictionCommand / Nobles & Greyson / Nobles and Greyson
- [x] Spanish script assertion updated: `"consulta gratuita"` (matches V2)
- [x] `docs/bland_ec_setup.md` uses new brand names throughout

---

## Phase 10 — Instantly.ai Sequences 🔲

**Goal:** Email sequences loaded and active for EC and NG leads.

- [ ] Load EC email sequence into Instantly.ai
- [ ] Load NG email sequence into Instantly.ai
- [ ] Verify sending domain is warmed and configured
- [ ] Wire `ghl_service.py` or separate `instantly_service.py` to enroll contacts on lead creation

---

## Phase 11 — Dummy E2E Test 🔲

**Goal:** One synthetic filing runs through the full pipeline without hitting real external services.

- [ ] Create `tests/test_e2e_pipeline.py` with a fully mocked pipeline run
  - Mock: BatchData, GHL, Bland, Supabase
  - Assert: filing inserted → enriched → routed → GHL contact created → Bland triggered
  - Assert: DNC-blocked contact does NOT trigger Bland
- [ ] Run: `pytest tests/test_e2e_pipeline.py -v`

---

## Phase 12 — May 11 Live Test 🔲

**Goal:** Real filing scraped, enriched, routed, GHL contact created, Bland call triggered — all with real credentials.

- [ ] Prerequisite: Phases 1–10 complete
- [ ] Set `AUTO_BLAND_CALLS_ENABLED=true` (requires explicit Chris/Zee approval)
- [ ] Trigger manual pipeline run via dashboard or `python jobs/run_california.py`
- [ ] Verify in Supabase: new filing row with `bland_triggered=true`
- [ ] Verify in GHL: contact appears in correct pipeline stage
- [ ] Verify in Bland: call appears in call log
- [ ] Revert `AUTO_BLAND_CALLS_ENABLED` to false after test

---

## Phase 13 — Supabase RLS Security 🔲

**Goal:** Supabase tables protected with Row Level Security so service role is the only writer.

- [ ] Enable RLS on `filings` table
- [ ] Enable RLS on `lead_contacts` table
- [ ] Enable RLS on `batchdata_cost_log` table
- [ ] Policy: service role can SELECT/INSERT/UPDATE; anon role has no access
- [ ] Test: anon key cannot read or write any table
- [ ] Document policy in `migrations/` as a new SQL file

---

## Railway Env Var Checklist

| Variable | Status | Action |
|---|---|---|
| `BLAND_EC_AGENT_ID` | ✅ Set | Verify pathway matches V2 script |
| `BLAND_NG_AGENT_ID` | ❌ Missing | Create pathway → set |
| `BLAND_NG_SPANISH_AGENT_ID` | ❌ Missing | Create pathway → set |
| `BLAND_EC_PHONE_NUMBER` | ✅ `+18186167276` | Verify this is GEG outbound |
| `BLAND_NG_PHONE_NUMBER` | ❌ Missing | Buy number → set |
| `BLAND_NG_SPANISH_PHONE_NUMBER` | ❌ Missing | Buy number → set |
| `BLAND_EC_CALLBACK_PHONE_NUMBER` | ❌ Missing | Set (may be same as EC phone) |
| `BLAND_NG_CALLBACK_PHONE_NUMBER` | ❌ Missing | Set |
| `BLAND_NG_SPANISH_CALLBACK_PHONE_NUMBER` | ❌ Missing | Set |
| `BLAND_ENABLED` | `false` | Set `true` before QA |
| `BLAND_TEST_CALLS_ENABLED` | Not set | Set `true` for QA, remove after |
| `AUTO_BLAND_CALLS_ENABLED` | Not set | Only enable with Chris/Zee approval |
| `GHL_EC_REVIEW_STAGE_ID` | ❌ Missing | Get from GHL → set |
| `GHL_WEBHOOK_SECRET` | ❌ Missing | Get from GHL → set |
| `PUSHOVER_USER_KEYS` | ✅ Set | Zee + Sunshine keys, comma-separated |

---

## Branch Status

| Branch | Status |
|---|---|
| `main` | Current live |
| `codex/dashboard-role-aware-views` | Phase 3 work — needs PR + merge |
