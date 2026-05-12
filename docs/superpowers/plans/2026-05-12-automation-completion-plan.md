# Lead Automation Completion Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the Grant Ellis Group / Vantage Defense Group lead automation from late integration to production-ready operation.

**Architecture:** Stabilize local code and tests first, then finish the outbound integration contracts for Instantly, Bland, GHL, and Supabase. Keep production outreach fail-closed: no calls, SMS, or email enrollment should happen unless explicit enable flags and required vendor IDs are present.

**Tech Stack:** Python async pipeline, pytest, Supabase/Postgres migrations, FastAPI dashboard, BatchData, GHL, Bland.ai, Instantly.ai, Railway.

---

## File Map

| File | Purpose |
|---|---|
| `pipeline/runner.py` | Main orchestration; currently has uncommitted Instantly return-shape changes |
| `services/instantly_service.py` | New uncommitted Instantly API wrapper |
| `services/bland_service.py` | Bland callback number support and voicemail payloads |
| `services/notification_service.py` | Pushover summary and Instantly failure alert text |
| `.env.example` | Needs all new operational env vars documented |
| `tests/test_runner_dnc_gate.py` | Must be updated to runner return contract or runner contract simplified |
| `tests/test_batchdata_optimization.py` | Must be updated if `_process_track` returns structured results |
| `tests/test_instantly_service.py` | New focused tests for Instantly gating, payloads, duplicate/blocklist behavior |
| `tests/test_e2e_pipeline.py` | New mocked end-to-end pipeline test |
| `migrations/010_run_metrics_instantly.sql` | Add `instantly_enrolled` if keeping metric in `run_metrics` |
| `migrations/011_rls_policies.sql` | Add Supabase RLS policies for service-role-only access |
| `docs/bland_ec_setup.md` | Bland setup status and callback variable alignment |
| `docs/ghl_sms_dnc_build_guide.md` | GHL/SMS/DNC/Instantly current state guide |

---

## Task 1: Stabilize The Dirty Workspace

**Files:**
- Inspect: `pipeline/runner.py`
- Inspect: `services/instantly_service.py`
- Inspect: `services/bland_service.py`
- Inspect: `services/notification_service.py`
- Inspect: `.env.example`

- [ ] **Step 1: Confirm current branch and dirty files**

Run:

```powershell
git status --short --branch
git diff --stat
```

Expected:

```text
## main...origin/main
 M .env.example
 M docs/bland_ec_setup.md
 M pipeline/runner.py
 M services/bland_service.py
 M services/notification_service.py
?? services/instantly_service.py
```

The exact untracked docs folders may also appear. Do not delete them.

- [ ] **Step 2: Run the current test baseline**

Run:

```powershell
pytest -q
```

Expected before fixes:

```text
9 failed, 119 passed, 2 skipped
```

The failures should be limited to runner return-shape fallout.

- [ ] **Step 3: Choose the runner contract**

Use a small result object instead of returning a loose tuple.

Add near the top of `pipeline/runner.py` after imports:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class TrackResult:
    ghl_created: bool
    instantly_enrolled: bool = False
    instantly_error: str | None = None
```

- [ ] **Step 4: Update `_process_track` return values**

Change the signature:

```python
async def _process_track(contact: EnrichedContact) -> TrackResult:
```

Replace returns like this:

```python
return TrackResult(False)
return TrackResult(True)
return TrackResult(True, instantly_enrolled=instantly_result.enrolled, instantly_error=instantly_result.error)
```

This keeps the meaning explicit and avoids tuple unpacking drift.

- [ ] **Step 5: Update runner aggregation**

Replace the result loop in `pipeline/runner.py`:

```python
for result in results:
    if result.ghl_created:
        m["ghl_created"] += 1
    if result.instantly_error:
        m.setdefault("instantly_failures", []).append(result.instantly_error)
    if result.instantly_enrolled:
        m["instantly_enrolled"] += 1
```

- [ ] **Step 6: Update existing tests for `TrackResult`**

In `tests/test_runner_dnc_gate.py`, replace assertions like:

```python
assert created is True
```

With:

```python
assert created.ghl_created is True
```

And:

```python
assert created.ghl_created is False
```

In `tests/test_batchdata_optimization.py`, update monkeypatched `process_track` helpers:

```python
async def process_track(contact: EnrichedContact) -> runner.TrackResult:
    return runner.TrackResult(ghl_created=True)
```

- [ ] **Step 7: Verify runner tests pass**

Run:

```powershell
pytest tests/test_runner_dnc_gate.py tests/test_batchdata_optimization.py -q
```

Expected:

```text
11 passed
```

- [ ] **Step 8: Commit stabilization**

Run:

```powershell
git add pipeline/runner.py tests/test_runner_dnc_gate.py tests/test_batchdata_optimization.py
git commit -m "fix: stabilize runner track result contract"
```

---

## Task 2: Finish Instantly Safely

**Files:**
- Modify: `services/instantly_service.py`
- Modify: `pipeline/runner.py`
- Modify: `.env.example`
- Create: `tests/test_instantly_service.py`
- Modify or create: `migrations/010_run_metrics_instantly.sql`

- [ ] **Step 1: Add explicit enable flag and result type**

In `services/instantly_service.py`, add:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class InstantlyResult:
    enrolled: bool = False
    skipped_reason: str | None = None
    error: str | None = None
```

Change `is_enabled()` to:

```python
def is_enabled() -> bool:
    return os.getenv("INSTANTLY_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
```

- [ ] **Step 2: Make `enroll` return structured status**

Change signature:

```python
async def enroll(contact: EnrichedContact) -> InstantlyResult:
```

Use these return meanings:

```python
if not is_enabled():
    return InstantlyResult(skipped_reason="disabled")
if not contact.email:
    return InstantlyResult(skipped_reason="missing_email")
if not campaign_id:
    return InstantlyResult(skipped_reason="missing_campaign_id")
if blocked:
    return InstantlyResult(skipped_reason="blocklisted")
if dupes and not uploaded:
    return InstantlyResult(skipped_reason="duplicate")
return InstantlyResult(enrolled=True)
```

HTTP and unexpected exceptions should return:

```python
return InstantlyResult(error=msg)
```

- [ ] **Step 3: Update runner use of Instantly**

In `pipeline/runner.py`, replace:

```python
instantly_err = await instantly_service.enroll(contact)
```

With:

```python
instantly_result = await instantly_service.enroll(contact)
```

And return:

```python
return TrackResult(
    True,
    instantly_enrolled=instantly_result.enrolled,
    instantly_error=instantly_result.error,
)
```

- [ ] **Step 4: Add Instantly tests**

Create `tests/test_instantly_service.py` with tests for:

```python
def test_instantly_disabled_without_flag(monkeypatch):
    monkeypatch.setenv("INSTANTLY_API_KEY", "key")
    monkeypatch.delenv("INSTANTLY_ENABLED", raising=False)
    assert instantly_service.is_enabled() is False


def test_instantly_enabled_with_flag(monkeypatch):
    monkeypatch.setenv("INSTANTLY_ENABLED", "true")
    assert instantly_service.is_enabled() is True
```

Add async tests for missing email, missing campaign ID, success response, duplicate response, blocklisted response, and HTTP error.

- [ ] **Step 5: Document Instantly env vars**

Add to `.env.example`:

```env
# Instantly.ai email enrollment. Default off; enables external API writes.
INSTANTLY_ENABLED=false
INSTANTLY_API_KEY=
INSTANTLY_EC_CAMPAIGN_ID=
INSTANTLY_NG_CAMPAIGN_ID=
```

- [ ] **Step 6: Add run metrics migration if needed**

Create `migrations/010_run_metrics_instantly.sql`:

```sql
ALTER TABLE run_metrics
ADD COLUMN IF NOT EXISTS instantly_enrolled INTEGER NOT NULL DEFAULT 0;
```

If `instantly_enrolled` should not be stored in Supabase yet, remove it from the metrics payload instead of adding this migration.

- [ ] **Step 7: Verify Instantly and full tests**

Run:

```powershell
pytest tests/test_instantly_service.py tests/test_runner_dnc_gate.py tests/test_batchdata_optimization.py -q
pytest -q
```

Expected:

```text
all tests passed, except known skipped scratch async tests if still present
```

- [ ] **Step 8: Commit Instantly safety**

Run:

```powershell
git add services/instantly_service.py pipeline/runner.py .env.example tests/test_instantly_service.py migrations/010_run_metrics_instantly.sql
git commit -m "feat: add gated instantly enrollment"
```

---

## Task 3: Align Bland Callback Configuration

**Files:**
- Modify: `services/bland_service.py`
- Modify: `.env.example`
- Modify: `docs/bland_ec_setup.md`
- Modify: `tests/test_brand_and_dnc.py`

- [ ] **Step 1: Decide callback variables**

Support three callback variables because docs already name all three:

```python
_EC_CALLBACK_NUMBER = os.getenv("BLAND_EC_CALLBACK_PHONE_NUMBER", "")
_NG_CALLBACK_NUMBER = os.getenv("BLAND_NG_CALLBACK_PHONE_NUMBER", "")
_NG_SPANISH_CALLBACK_NUMBER = os.getenv("BLAND_NG_SPANISH_CALLBACK_PHONE_NUMBER", "")
```

- [ ] **Step 2: Add helper for callback number**

In `services/bland_service.py`, add:

```python
def _callback_number_for_contact(contact: EnrichedContact) -> str:
    from_number = _phone_number_for_contact(contact) or "[PHONE_NUMBER]"
    if contact.track == "ec":
        return _EC_CALLBACK_NUMBER or from_number
    if _is_spanish_likely(contact):
        return _NG_SPANISH_CALLBACK_NUMBER or _NG_CALLBACK_NUMBER or from_number
    return _NG_CALLBACK_NUMBER or from_number
```

- [ ] **Step 3: Use callback helper in script rendering and payload**

Replace inline callback logic in `render_voicemail_script()` and `trigger_voicemail()` with:

```python
callback = _callback_number_for_contact(contact)
```

Use callback in `request_data`:

```python
"ec_phone" if is_ec else "ng_phone": callback,
```

- [ ] **Step 4: Document callback env vars**

Add to `.env.example`:

```env
BLAND_EC_CALLBACK_PHONE_NUMBER=
BLAND_NG_CALLBACK_PHONE_NUMBER=
BLAND_NG_SPANISH_CALLBACK_PHONE_NUMBER=
```

- [ ] **Step 5: Add tests for callback behavior**

In `tests/test_brand_and_dnc.py`, add tests that set callback env vars, reload `bland_service`, and assert rendered scripts use callback numbers instead of outbound numbers.

- [ ] **Step 6: Verify Bland tests**

Run:

```powershell
pytest tests/test_brand_and_dnc.py -q
```

Expected:

```text
all passed
```

- [ ] **Step 7: Commit Bland alignment**

Run:

```powershell
git add services/bland_service.py .env.example docs/bland_ec_setup.md tests/test_brand_and_dnc.py
git commit -m "fix: align bland callback number configuration"
```

---

## Task 4: Add Mocked End-To-End Pipeline Coverage

**Files:**
- Create: `tests/test_e2e_pipeline.py`
- Possibly modify: `pipeline/runner.py`

- [ ] **Step 1: Add happy-path EC test**

Create a test where all external services are mocked:

```python
@pytest.mark.asyncio
async def test_pipeline_happy_path_ec_queues_bland_without_auto_call(monkeypatch):
    monkeypatch.setattr(runner, "_NG_ENABLED", False)
    monkeypatch.setattr(runner, "_AUTO_BLAND_CALLS_ENABLED", False)
    # Mock Supabase, BatchData, GHL, Bland, notifications.
    # Assert filing inserted, enrichment saved, GHL created, Bland status pending.
```

- [ ] **Step 2: Add DNC-blocked path**

Add:

```python
@pytest.mark.asyncio
async def test_pipeline_dnc_blocked_never_triggers_bland(monkeypatch):
    # Return EnrichedContact(dnc_status="blocked")
    # Assert bland_service.trigger_voicemail is never called.
    # Assert set_bland_status receives "blocked_dnc".
```

- [ ] **Step 3: Add EC + NG path**

Add:

```python
@pytest.mark.asyncio
async def test_pipeline_ec_and_ng_tracks_are_processed_separately(monkeypatch):
    # Enable _NG_ENABLED.
    # Return EC landlord and NG tenant contacts.
    # Assert two GHL contacts, two lead_contacts updates, correct tracks.
```

- [ ] **Step 4: Run E2E tests**

Run:

```powershell
pytest tests/test_e2e_pipeline.py -q
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Run full suite**

Run:

```powershell
pytest -q
```

Expected:

```text
all tests passed, known scratch skipped only
```

- [ ] **Step 6: Commit E2E coverage**

Run:

```powershell
git add tests/test_e2e_pipeline.py
git commit -m "test: add mocked pipeline e2e coverage"
```

---

## Task 5: Finish Supabase Migrations And RLS

**Files:**
- Create: `migrations/011_rls_policies.sql`
- Inspect: `migrations/001_init.sql`
- Inspect: `migrations/009_lead_contacts.sql`

- [ ] **Step 1: Confirm current tables**

Run against Supabase SQL editor or CLI:

```sql
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('filings', 'lead_contacts', 'batchdata_cost_log', 'run_metrics');
```

Expected all four rows exist after migrations.

- [ ] **Step 2: Add RLS policy migration**

Create `migrations/011_rls_policies.sql`:

```sql
ALTER TABLE filings ENABLE ROW LEVEL SECURITY;
ALTER TABLE lead_contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE batchdata_cost_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_metrics ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service_role_all_filings" ON filings;
CREATE POLICY "service_role_all_filings"
ON filings
FOR ALL
USING (auth.role() = 'service_role')
WITH CHECK (auth.role() = 'service_role');

DROP POLICY IF EXISTS "service_role_all_lead_contacts" ON lead_contacts;
CREATE POLICY "service_role_all_lead_contacts"
ON lead_contacts
FOR ALL
USING (auth.role() = 'service_role')
WITH CHECK (auth.role() = 'service_role');

DROP POLICY IF EXISTS "service_role_all_batchdata_cost_log" ON batchdata_cost_log;
CREATE POLICY "service_role_all_batchdata_cost_log"
ON batchdata_cost_log
FOR ALL
USING (auth.role() = 'service_role')
WITH CHECK (auth.role() = 'service_role');

DROP POLICY IF EXISTS "service_role_all_run_metrics" ON run_metrics;
CREATE POLICY "service_role_all_run_metrics"
ON run_metrics
FOR ALL
USING (auth.role() = 'service_role')
WITH CHECK (auth.role() = 'service_role');
```

- [ ] **Step 3: Verify anon cannot read**

Using the anon key, run:

```sql
SELECT * FROM filings LIMIT 1;
```

Expected:

```text
0 rows or permission denied, depending on Supabase client context
```

It must not return lead data.

- [ ] **Step 4: Verify service role can read/write**

Using service role, run a safe select:

```sql
SELECT case_number FROM filings LIMIT 1;
```

Expected: query succeeds.

- [ ] **Step 5: Commit migration**

Run:

```powershell
git add migrations/011_rls_policies.sql
git commit -m "feat: add service-role rls policies"
```

---

## Task 6: Vendor Configuration Checklist

**Files:**
- Modify: `docs/bland_ec_setup.md`
- Modify: `docs/ghl_sms_dnc_build_guide.md`

- [ ] **Step 1: Bland.ai setup**

Complete in Bland UI:

```text
Grant Ellis Group Outbound pathway created
Vantage Defense Group English Outbound pathway created
Vantage Defense Group Spanish Outbound pathway created
EC outbound number confirmed
NG English outbound number purchased
NG Spanish outbound number purchased
Local Presence add-on decision recorded
```

Set Railway:

```env
BLAND_ENABLED=true
BLAND_TEST_CALLS_ENABLED=true
BLAND_EC_AGENT_ID=
BLAND_NG_AGENT_ID=
BLAND_NG_SPANISH_AGENT_ID=
BLAND_EC_PHONE_NUMBER=
BLAND_NG_PHONE_NUMBER=
BLAND_NG_SPANISH_PHONE_NUMBER=
BLAND_EC_CALLBACK_PHONE_NUMBER=
BLAND_NG_CALLBACK_PHONE_NUMBER=
BLAND_NG_SPANISH_CALLBACK_PHONE_NUMBER=
AUTO_BLAND_CALLS_ENABLED=false
```

- [ ] **Step 2: GHL setup**

Set Railway:

```env
GHL_API_KEY=
GHL_API_NG_KEY=
GHL_EC_LOCATION_ID=
GHL_NG_LOCATION_ID=
GHL_NEW_FILING_STAGE_ID=
GHL_EC_REVIEW_STAGE_ID=
GHL_NG_NEW_FILING_STAGE_ID=
GHL_NG_COMMERCIAL_STAGE_ID=
GHL_WEBHOOK_SECRET=
```

Verify in GHL:

```text
EC contact upsert works
NG contact upsert works
EC opportunity stage resolves
NG residential stage resolves
NG commercial stage resolves
Required custom fields exist
DNC Cleared field exists if used by workflows
```

- [ ] **Step 3: Instantly setup**

Create campaigns in Instantly:

```text
Grant Ellis Group landlord sequence
Vantage Defense Group tenant sequence
Sending domain warmed and authenticated
Daily limits set conservatively
```

Set Railway only when ready:

```env
INSTANTLY_ENABLED=true
INSTANTLY_API_KEY=
INSTANTLY_EC_CAMPAIGN_ID=
INSTANTLY_NG_CAMPAIGN_ID=
```

- [ ] **Step 4: GHL SMS and A2P**

In GHL UI:

```text
A2P registration submitted for current brands
Grant Ellis Group SMS workflow created
Vantage Defense Group English SMS workflow created
Vantage Defense Group Spanish SMS workflow created
STOP and ALTO opt-out behavior verified
Reply handling pauses automation and creates owner task
Sending window set to 8 AM - 7 PM local time
DNC Cleared required before any SMS
```

- [ ] **Step 5: Update docs**

Update status tables in:

```text
docs/bland_ec_setup.md
docs/ghl_sms_dnc_build_guide.md
```

- [ ] **Step 6: Commit docs**

Run:

```powershell
git add docs/bland_ec_setup.md docs/ghl_sms_dnc_build_guide.md
git commit -m "docs: update vendor setup status"
```

---

## Task 7: Safe Live QA

**Files:**
- No code changes expected unless QA finds defects

- [ ] **Step 1: Push code**

Run:

```powershell
git status --short
git log --oneline -n 5
git push origin main
```

Expected: clean working tree and push succeeds.

- [ ] **Step 2: Run scraper-only smoke tests**

Run only scraper-safe commands:

```powershell
python scripts/smoke_scrapers.py --states texas,tennessee --notify
```

Expected:

```text
No BatchData, GHL, Bland, or Instantly writes.
Summary notification sends if Pushover is enabled.
```

- [ ] **Step 3: Run dashboard QA**

Open Railway dashboard and verify:

```text
Grant tab loads landlord contacts
Vantage tab loads tenant contacts
Spanish views show Spanish-likely leads
Ready to Call count matches clear-DNC leads with phone
Approve is blocked for unknown or blocked DNC
Manual DNC Clear does not trigger Bland
Skip updates only the selected track
```

- [ ] **Step 4: Run Bland QA calls**

Only after explicit approval, trigger internal QA calls:

```text
Grant QA call to internal number
Vantage English QA call to internal number
Vantage Spanish QA call to internal number
```

Expected:

```text
Correct brand
Correct callback number
Correct language
No legal promises
No old brand names
```

- [ ] **Step 5: Run one controlled live E2E**

Only after explicit approval:

```env
AUTO_BLAND_CALLS_ENABLED=true
```

Run one selected state/county job. Verify:

```text
Supabase filing inserted once
lead_contacts has EC and/or NG rows
DNC status stored
GHL contact and opportunity created
Bland call triggered only for clear-DNC phone
Instantly enrollment occurs only when enabled and email exists
Run metrics saved
Pushover summary received
```

Immediately restore:

```env
AUTO_BLAND_CALLS_ENABLED=false
```

- [ ] **Step 6: Final production readiness note**

Create or update an Obsidian/session note with:

```text
Commit SHA deployed
Migrations applied
Railway env vars verified
QA call outcomes
Live E2E outcome
Remaining manual operations
```

---

## Acceptance Criteria

- [ ] `pytest -q` passes locally, with only known intentional skips.
- [ ] Dirty workspace is resolved into intentional commits.
- [ ] Instantly cannot write externally unless `INSTANTLY_ENABLED=true`.
- [ ] Bland cannot auto-call unless `AUTO_BLAND_CALLS_ENABLED=true`.
- [ ] DNC remains fail-closed for runner and dashboard approval.
- [ ] Supabase migrations are applied and RLS policies are verified.
- [ ] Dashboard shows correct EC/NG track data.
- [ ] GHL contacts and opportunities route to correct brand/stage.
- [ ] QA calls pass for EC, NG English, and NG Spanish.
- [ ] One controlled live E2E succeeds and auto-calling is disabled afterward.

