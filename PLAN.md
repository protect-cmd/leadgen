# Plan: Get the 4 businesses live on one clean, hands-off pipeline
_Locked via grill — by Claude + Zee. Revised after Codex Round 1._

## Goal
Get all four lead-gen businesses running on **shared pipeline stages with per-business
adapters** so the system operates hands-off every day and only ever spends money on
quality leads. The four: **Vantage/VDG** (eviction × filed, stored in `filings` +
`lead_contacts`), **ISTS** (eviction × judgment, `ists_judgments`), **Cosner Drake**
(debt × filed, `cosner_filings`), and **Garnish Proof** (debt × judgment,
`garnishment_orders`, fed *now* by a manual Florida/Hillsborough garnishment-writ
spreadsheet import while an automated scraper is deferred). "Live and clean" means: a
single trunk (`main`) that matches production; every keeper scraper scheduled and
producing fresh, address-complete data; a daily ranked "good leads" queue ready **by
9 PM PHT (13:00 UTC)** per business; all paid actions (SearchBug, GHL, Instantly,
Bland) gated by **one shared pre-external-action guard** (quality floor + DNC + atomic
per-business daily cap) so a thin day costs less rather than producing junk; and a
**reliable scheduled health monitor** covering every business/table that pings Pushover
daily and alerts loudly on failure. Operator stays hands-off except at three deliberate
gates: spending money, destructive git, and pushing leads outward.

## Spend-authority model (resolves the "hands-off vs spending money" tension)
The per-business **daily caps ARE the standing pre-approval**: within a cap, the daily
scheduled flow (enrich within cap → stage to GHL → dial via Bland within the Bland
100/day policy) runs automatically, hands-off. The three human hard-stop gates apply to
actions **outside** the standing policy: (a) raising a cap or any ad-hoc/extra paid
batch, (b) destructive git, (c) initial go-live activation and any new outward channel.
So "hands-off" and "stop to spend money" don't conflict: routine in-policy spend is
auto; only policy *changes* and one-offs stop for Zee.

## Key architectural correction (from Codex Round 1)
The four businesses already have **separate persistence models** — there is no single
canonical row, and we will NOT force one. "One pipeline" means: shared *stages* and a
shared *contract*, with a thin **per-business adapter** mapping each business's table to
the contract. The contract is **three layers, not one record**:
- **RawCourtRecord** — what a source (scraper or spreadsheet) produces: `case_number`,
  `first_name`, `last_name`, `street/city/state/zip`, `amount` + `amount_kind`
  (nullable), `freshness_date` (filed/judgment/writ per business), `county`, `state`,
  `business`, `creditor/plaintiff`, deadline fields (e.g. GP `exemption_deadline`),
  `source_hash`, `raw_source`.
- **LeadCandidate** — post-gate/score: contract record + `score`, `floor_pass`,
  `freshness_ok`, `enrichment_attempt_state`.
- **OutreachState** — `dnc_status`, GHL/Instantly/Bland state, `enriched_at`,
  `last_called_at`. Each layer has explicit required keys per stage.

## Approach

### Phase 0 — Consolidate to one clean trunk (before any new code)
1. **Inventory the dirty worktree first.** It currently spans scheduler, Texas job,
   dedup, health, tests, models, and many new files. Split into isolated, focused
   commits by concern; require focused tests per commit. Do NOT bulk-"land current
   changes" — that risks baking half-built behavior into trunk.
2. Inventory the ~25 branches + 11 open PRs into a keep / kill / supersede table mapped
   to a business (initial read unchanged: keep #48, #24, #41; review scraper PRs #51,
   #49, #44, #21, #15, #9, #11, `feat/duval-eviction-scraper`, `add-lorain-scraper`;
   supersede #43 Hillsborough; decide ambiguous with Zee).
3. Merge keepers into `main` one focused PR at a time; close/delete dead branches.
   Establish `main` as source of truth and **verify Railway deploys from `main`**
   (deployed == committed) rather than assuming it.

### Phase 1 — Shared contract + per-business adapters
4. Define the three-layer contract above as typed models. For each business write a thin
   adapter mapping its existing table ↔ contract, with explicit column maps and any
   migration/backfill steps. No table unification.

### Phase 2 — Split the runner into discrete stages
5. `pipeline/runner.run` currently does ingest + enrich + GHL + Instantly + Bland in one
   loop. Split into explicit commands: **`ingest_only`**, **`enrich`**, **`stage`**
   (GHL/Instantly), **`fire`** (Bland). Every business invokes the same four stages via
   its adapter.
6. Make all scrapers **ingest-only** (scrape → clean → dedup → upsert → gate). The
   Texas inline SearchBug burn disappears because enrichment moves to its own stage.
   **Persist rejected rows / rejection reasons** (don't silently drop) so pass-rate and
   freshness observability survives gating — gating only removes a row from the *paid*
   queue, not from storage/metrics.
7. Apply shared gates (`pipeline/gates.py`) identically: address, name, freshness.
8. **Queue/fire adapter registry:** current queue/dashboard/fire surfaces cover only
   Vantage + ISTS. Add a registry so Cosner Drake and Garnish Proof get queue, fire, and
   dashboard/API coverage through the same stages.

### Phase 3 — Fix data loss (dedup + upsert) — staged schema migration
9. **Duplicate-check collision (root cause):** `dedup_service.is_duplicate(case_number)`
   checks `filings` **globally by case number only**; court case numbers (e.g.
   `09-CC-001607`) are reused across counties, so new filings collide and are silently
   dropped. But `case_number` is the **primary key** on `filings`, `ists_judgments`,
   `cosner_filings`, and `garnishment_orders`, and `lead_contacts` keys by
   `(case_number, track)` — so this is a **staged schema migration**, not a code tweak:
   introduce a stable `lead_id` or composite source key `(business, county, case_number)`,
   migrate FKs/views/scripts/tests to it, **then** change dedup logic. Sequence and test
   each migration step; no big-bang.
10. **Clobbering upserts:** `lead_contacts.upsert` sends nullable phone/email; `cd_store`
    and `gp_store` use blind upsert helpers. Replace blind upserts with column-specific
    update / RPC using `COALESCE` / "only fill blank" rules so enrichment, phone,
    `enriched_at`, and outreach columns are **never overwritten** on an already-seen row.

### Phase 4 — Unify DNC vocabulary
11. DNC enum is inconsistent: queue expects `callable/dnc/unknown`; dashboard uses
    `clear/blocked/unknown`. Pick one enum, add a migration + compatibility mapper, and
    update queue builders, dashboard, tests, and docs together.

### Phase 5 — Two-stage spend guard (money safety)
12. DNC cannot be evaluated before SearchBug returns a phone, so the guard is **two
    stages**, not one:
    - **Pre-enrichment guard** (before SearchBug): **pre-paid quality floor** (valid
      split name + real street address + inside freshness window + **pre-paid score** —
      see Phase 6) **+ quota reservation**.
    - **Post-enrichment guard** (before GHL/Instantly/Bland): **DNC** + downstream
      channel checks. Today Vantage creates GHL + enrolls Instantly *before* the DNC
      gate — this guard fixes that ordering so no outward channel fires on a
      DNC-blocked/below-floor lead.
13. **Atomic quota service:** today's cap is local SQLite inside the enrichment cache,
    which ISTS/GP/Cosner bypass. Build one DB-backed quota service used by every paid
    path, with explicit **state transitions** (reserve → commit → rollback on
    failure/retry) and **idempotency keys per (business, action, lead, day)** so retries
    and "already enriched" replays never double-spend. Per-business **daily cap** sits on
    top; hard stop at cap; never pad below the floor even when under cap.

### Phase 6 — Two-stage, per-business scoring profiles
14. `pipeline/lead_score.py` is a single rent/name/freshness scorer for tenant eviction
    leads, and it depends on estimated rent — which is **enrichment-derived**, making a
    naive "floor = score ≥ threshold" circular. Split scoring:
    - **Pre-paid score** — uses only scraper/import fields + free/static data (our ZIP
      rent medians, freshness, name/address quality). This feeds the Phase 5
      pre-enrichment floor.
    - **Post-enrichment rescore** — may use enrichment-derived fields; used for final
      ranking/fire ordering only, never to authorize the paid lookup.
    Implement and unit-test a **profile per business** that selects that business's
    target candidate, before any threshold/cap becomes a production gate.

### Phase 7 — Garnish Proof importer
15. Build a repeatable importer for the Hillsborough garnishment-writ spreadsheet → the
    contract → `garnishment_orders`. Specify: spreadsheet→column mapping; split
    `LAST, FIRST`; address parse + **debtor-home-address validation**; **county default
    Hillsborough** (the table currently defaults Miami-Dade); freshness = writ filed
    date mapped to the schema's `filing_date`; `amount` null; `garnishment_type`;
    derive `exemption_deadline`; **import batch/source hash for idempotency** so
    re-imports don't duplicate.

### Phase 8 — Scheduling backward from 13:00 UTC
16. Define per-source scrape-completion SLAs and schedule **scrape → enrich → queue →
    health-check backward from 13:00 UTC** (today the first scrape *starts* at 13:00 and
    the last at 15:30 — contradicting "ready by 13:00"). Move scrapes earlier — but
    **respect per-portal earliest-safe-run constraints first** (Harris portal
    maintenance/timing windows, residential-IP availability for Hillsborough, etc.). If
    a portal can't safely run early enough, that business's SLA is documented as an
    exception rather than forced. Stagger to avoid Harris/Cloudflare stacking.
17. Schedule every keeper scraper (today only 7 run; add Montgomery OH + merged keepers).

### Phase 9 — Reliable monitoring + alerting
18. Extend `verify_pipeline_health.py` to cover **every business/table** (it currently
    maps only filing-table jobs and lacks `cosner_drake`; Cosner/GP live in separate
    tables). Add scheduler + Pushover integration **as code** (a real scheduled job),
    not a manual wrapper. Always send a daily Pushover summary; alert loudly on FAIL
    (scraper dark, freshness stale, schema drift). **Cap/quota exhaustion is reported as
    a "budget-limit" status, NOT a FAIL** — it only escalates to alert when paired with
    an anomaly (e.g. unusually low floor-pass volume or an unexpected call count), so a
    healthy capped day doesn't page Zee.

### Phase 10 — Go-live verification
19. Dry-run all four businesses end-to-end. Confirm: deployed == committed; both guard
    stages block paid paths when tripped (attempt to over-spend and watch it stop);
    monitor fires + pings Pushover; good-leads queue ranked and ready before 13:00 UTC.
20. **Test bar for go-live:** migration-order tests (each staged migration applies
    cleanly in sequence), focused per-stage tests (ingest/enrich/stage/fire), quota
    reserve/commit/rollback + idempotency tests, and a green `pytest -q` regression run.

## Key decisions & tradeoffs
- **Shared stages + per-business adapters over existing tables** — not one canonical
  table. Lower migration risk; respects what already works.
- **Three-layer contract** (Raw → Candidate → OutreachState) instead of one fat record.
- **Consolidate before standardizing**, and **split the dirty worktree into focused
  commits** before merging — avoids baking half-built behavior into trunk.
- **Safety = a two-stage spend guard** — pre-enrichment (pre-paid floor + quota
  reservation) and post-enrichment (DNC + channel) — covering SearchBug, GHL, Instantly,
  Bland. Quality over quantity; thin days cost less; accidental burn is structurally
  impossible because nothing below the floor reaches a paid step.
- **Split runner into ingest/enrich/stage/fire** — the real shape of "uniform scrapers".
- **COALESCE/"only fill blank" upserts + composite dedup key** — the data-loss fix.
- **Per-business scoring profiles + DNC enum unification** are prerequisites, not
  afterthoughts.
- **GP live now via a fully-specified spreadsheet importer** into `garnishment_orders`;
  automated GP scraper deferred behind the same contract.
- **Implementation handoff:** converged plan can go to Codex CLI to implement (token
  economy) with Claude orchestrating/reviewing, or Claude implements — decided at sign-off.

## Risks / open questions
- Branch/PR triage and worktree split may surface half-built scrapers or conflicts.
- Per-business scoring thresholds and daily caps need calibration with Zee against real
  volumes (exact numbers TBD).
- Atomic quota service design must survive concurrent stage runs across businesses.
- Railway "deploys from main" must be verified.
- GP automated-scraper feasibility (reCAPTCHA/legal sourcing) remains open; manual import
  is the bridge. Exemption-deadline derivation rules need confirmation against FL law.

## Out of scope
- Building the automated Garnish Proof scraper (deferred; manual import bridges it).
- New geographies beyond the triaged keepers.
- GP Bland voice/prompt redesign.
- Table unification / re-platforming the per-business stores.
