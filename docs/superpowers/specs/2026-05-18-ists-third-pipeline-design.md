# I Stopped The Sheriff — Third Pipeline Design

## Summary

I Stopped The Sheriff (ISTS) should be added to the current automation platform as a **third first-class pipeline**, alongside:

- Grant Ellis Group (`geg`)
- Vantage Defense Group (`vdg`)
- I Stopped The Sheriff (`ists`)

ISTS should **not** be implemented as:

- a cloned copy of the existing repo, or
- a thin extension of the current Vantage Defense Group tenant flow.

The best approach is to keep one shared platform, reuse the existing infrastructure, and add ISTS as a separate brand/program with its own source logic, outreach policy, GHL configuration, and dashboard views.

## Recommendation

Use a **targeted multi-brand extension**:

1. Reuse the current shared services:
   - scraper framework
   - deduplication
   - BatchData integration
   - DNC/callability gates
   - Bland.ai client
   - GHL client
   - scheduler
   - metrics and dashboard shell
2. Add deliberate seams for brand-specific behavior:
   - program identity
   - lead event type
   - brand-specific GHL mappings
   - brand-specific Bland scripts and voices
   - brand-specific call-window policy
   - dashboard registration

This gives ISTS a clean home without turning the current repo into a large pre-launch refactor project.

## Why Not Duplicate The Project

A second repo would feel simpler initially, but it would duplicate every shared concern:

- scraper fixes
- BatchData logic
- DNC protections
- GHL integration changes
- Bland behavior
- scheduler improvements
- dashboard maintenance

Over time, the two systems would drift and the cost of maintaining them would exceed the short-term convenience of separation.

## Why ISTS Is Not Just Another Vantage View

ISTS is tenant-facing, but it is operationally distinct from the existing Vantage Defense Group workflow.

| Dimension | Vantage Defense Group | I Stopped The Sheriff |
|---|---|---|
| Trigger | New eviction filing | Final judgment, later sheriff notice |
| Timing | Standard lead flow | Urgency windows |
| Outreach logic | Existing tenant pipeline | Window 1 vs Window 2 |
| Dashboard need | Filing review | Judgment status, attempts, callbacks |
| Brand | Vantage Defense Group | I Stopped The Sheriff |
| GHL setup | Existing tenant workflows | Separate sub-account and pipeline |

ISTS therefore needs a separate program identity, even though it can reuse tenant-side enrichment capabilities.

## Product Shape

The platform should support three first-class brands:

| Brand | Audience | Lead Trigger |
|---|---|---|
| Grant Ellis Group | Landlord / owner | New eviction filing |
| Vantage Defense Group | Tenant | New eviction filing |
| I Stopped The Sheriff | Tenant | Final judgment, then sheriff notice |

## Architecture Principle

Keep a clean separation between:

### Shared infrastructure

- scraper framework
- storage and dedupe
- BatchData access
- DNC and callability checks
- Bland.ai transport
- GHL transport
- scheduler
- observability and metrics

### Brand-specific policy

- source eligibility
- qualification rules
- enrichment behavior
- GHL destination
- Bland script and voice selection
- call windows
- callback tags and urgency
- dashboard views and metrics

The implementation should avoid scattering `if brand == "ists"` branches throughout existing Vantage logic. ISTS should live behind clear brand/program boundaries.

## Data Model Direction

ISTS introduces business concepts that do not map cleanly to a filing-only model. The design should support:

- `program` / `brand`: `geg`, `vdg`, `ists`
- `track`: landlord vs tenant
- `lead_event_type`: `filing`, `final_judgment`, `sheriff_notice`
- `judgment_date`
- `sheriff_notice_date`
- `outreach_window`: `w1`, `w2`
- `call_attempt_count`
- `last_call_at`
- `callback_priority`
- `source_record_type`
- `batchdata_mobile_clean`

The current system already uses shared case storage plus per-track contact records. Before implementation, the repo should be checked to decide whether ISTS is best represented by:

1. broadening the current filing-centric model, or
2. introducing a sibling event model for judgment and sheriff-notice records.

The chosen model should make it possible to deduplicate:

- a new final judgment
- a later sheriff-notice update
- repeated daily scrapes of the same event

without losing the relationship between those records.

## ISTS Pipeline Flow

For Maricopa County, the daily ISTS flow should be:

1. Scrape final UD judgments from the Maricopa portal.
2. Normalize:
   - defendant name
   - property address
   - judgment metadata
3. Deduplicate on stable court identity plus event type.
4. Persist the record before enrichment.
5. Run tenant enrichment through BatchData.
6. Store:
   - phone
   - email
   - selected phone metadata
   - whether a clean mobile was found
7. Emit daily hit-rate metrics:
   - judgments scraped
   - records enriched
   - any phone found
   - clean mobile found
   - clean mobile percentage
8. Assign outreach window:
   - `W1` for fresh judgments
   - `W2` once sheriff notice is detected
9. Apply call gates:
   - DNC must be explicitly clear
   - Window 1: 9:00 AM–6:00 PM local tenant time
   - Window 2: 8:00 AM–7:00 PM local tenant time
   - Sunday: no calls before 10:00 AM
   - maximum 3 attempts per number
10. Trigger Bland.ai only when all gates pass.
11. Create or update the ISTS GHL contact and opportunity.
12. Start the appropriate SMS sequence.
13. Persist call status, recording metadata, and callback tags.

## Bland.ai Design

ISTS should have its own outreach policy and script family.

### Required configuration

- English voice: Marcus
- Spanish voice: Diego
- script family:
  - `W1` — fresh judgment
  - `W2` — sheriff notice posted
- voicemail behavior: leave the full message
- maximum attempts: 3

### Callback tagging

- `W1 Callback` — standard priority
- `W2 Callback URGENT` — immediate closer pickup

### Safety rule

The current repository-level guardrail remains in force:

- Bland.ai auto-calling stays disabled unless explicitly enabled.
- Local/test runs must not send production outreach without explicit approval.

## GHL Design

ISTS should be implemented as its own GHL sub-account under the existing account structure.

### Pipeline stages

1. New Lead
2. Bland.ai Called
3. SMS Sent
4. Callback Received
5. Closer Assigned
6. Qualifying
7. Quoted
8. Closed Won
9. Docs Sent
10. Attorney Referral
11. Closed Lost

### Expected behavior

- Create the contact once a valid ISTS lead is accepted.
- Create the opportunity in `New Lead`.
- Advance stages from Bland / Make / GHL events.
- Store call recordings on the GHL contact record.
- Apply callback urgency tags based on the outreach window.
- Trigger the Stripe/payment workflow from `Closed Won`.

## Dashboard Design

The current dashboard should evolve from two brand tabs to three:

- `GRANT`
- `VANTAGE`
- `ISTS`

ISTS should use the same dashboard shell but have its own operating views.

### Suggested ISTS views

- Fresh Judgments
- Sheriff Notices
- Needs Review
- Called
- Callbacks
- Closed / Archived

### Suggested ISTS columns

- defendant
- property address
- county
- judgment date
- outreach window
- phone
- clean mobile status
- DNC status
- attempt count
- last call
- callback priority
- GHL stage

### Suggested ISTS top metrics

- judgments scraped
- BatchData hit rate
- clean mobiles found
- ready to call
- Window 1 queued
- Window 2 urgent
- callbacks
- closed won

ISTS should be a distinct dashboard workspace, not merely a re-labeled Vantage view.

## Delivery Sequence

### Phase A — Foundation

- Add ISTS brand/program support.
- Introduce lead-event concepts needed for judgment-based workflows.
- Add brand-specific configuration seams.
- Extend the dashboard to register a third brand.

### Phase B — Maricopa MVP

- Build the Maricopa final-judgment scraper.
- Run scraper-only proof tests.
- Wire BatchData enrichment.
- Record clean-mobile hit-rate metrics.
- Add ISTS GHL mappings.
- Add Bland `W1` / `W2` configuration.
- Define the Make.com handoff contract.

### Phase C — Production Hardening

- Enforce call windows.
- Enforce max-attempt logic.
- Add callback tagging and urgency handling.
- Persist recording references.
- Harden retries and failures.
- Validate dashboard metrics and review flows.

### Phase D — County Expansion

Reuse the same ISTS pipeline contract and add source adapters for:

1. Harris County, Texas
2. Miami-Dade County, Florida
3. Clark County, Nevada
4. Cook County, Illinois

## Scope Boundaries

To keep the launch focused:

- Do not clone the repo.
- Do not merge ISTS into Vantage logic.
- Do not build a generalized workflow engine before launch.
- Do not move business rules exclusively into Make.com.
- Do not overbuild future sheriff-notice features before Maricopa proves out.

## Main Risks To Resolve Early

1. **Maricopa source quality**
   - Can the portal reliably expose final UD judgments with defendant name and property address?
2. **Event identity**
   - How should final judgments and later sheriff notices be linked without duplicate leads?
3. **Phone quality**
   - What is the actual clean-mobile hit rate from BatchData for post-judgment tenant records?
4. **Timing compliance**
   - Can local-time windows and Sunday restrictions be enforced correctly and consistently?
5. **Workflow sprawl**
   - Can Make.com remain an orchestration layer rather than becoming the hidden source of truth for business logic?

## Final Recommendation

Add **I Stopped The Sheriff** to the current automation as a **third first-class pipeline**:

- one shared codebase
- shared infrastructure
- isolated brand-specific policy
- separate dashboard workspace
- separate GHL sub-account and pipeline

This is the best balance between speed, reuse, and long-term maintainability.
