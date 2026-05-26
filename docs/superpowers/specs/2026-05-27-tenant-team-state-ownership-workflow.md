# Tenant Team State-Ownership Workflow

**Date:** 2026-05-27
**Author:** Zee (tech lead)
**Status:** Approved (email sent to Chris 2026-05-27)

## Problem

The original team brief (Chris, 2026-05-26) assigns each builder a single court source. This wastes the three coder-capable builders — Nourul, Franz, Donnald — in two ways:

1. A single source is 2–3 weeks of work, after which the builder sits idle waiting for Lorraine to feed them the next one. The researcher becomes a throughput bottleneck.
2. Court portal discovery is itself an engineering task (probing endpoints, parsing HTML, testing date enumeration, reading API documentation). The coders are better at it than a pure researcher; assigning them as build-only operators leaves that capability unused.

Pattern recognition also compounds when a builder owns a state — Tyler-portal familiarity accumulates across counties, non-Tyler clerk-site familiarity accumulates across counties — and that compounding doesn't happen if builders jump between unrelated sources every two weeks.

## The Change

Each coder-builder owns a state. They discover counties within it, classify them green/yellow/red against `source_discovery_matrix.md`, and build the greens. Lorraine and Recca move off the tenant track entirely onto ShieldDesk.

## State Assignments

| Builder | Starting state | Off-limits (already built) | Active queue |
|---|---|---|---|
| Nourul | Texas | Harris JP Extracts, Tarrant JP | Travis, Williamson, Fort Bend, Montgomery, Brazoria, Collin, Denton, Galveston, El Paso |
| Franz | Ohio | Hamilton Municipal, Franklin Municipal | Cuyahoga, Montgomery (Dayton), Summit, Lucas, Butler, Stark, Lorain |
| Donnald | Tennessee | Davidson General Sessions | Shelby, Knox, Hamilton TN, Sumner, plus any TN county not yet on matrix |

**Fit rationale:**

- **Nourul → Texas** matches his Tyler-portal specialty (Travis, Williamson, Fort Bend are all Tyler Odyssey/PAW). TX is also the highest-volume state on the matrix — Tarrant JP alone is ~1,000 filings/week.
- **Franz → Ohio** matches his non-Tyler specialty — every Ohio source is non-Tyler (clerk portals, static HTML, direct clerk sites). An Ohio Court of Appeals public-access ruling on Hamilton is leverage when other county portals push back.
- **Donnald → Tennessee** matches his generalist profile (Google Maps, public directories, court portals). TN has the most diverse stack: PDF dockets (Knox, Sumner), JSON API (Hamilton TN), anomalous Shelby "download case info" page. Shelby is the single highest-upside untested county on the matrix.

**Backlog (next state after current is exhausted):**

- Nourul → Arizona
- Franz → California
- Donnald → Kansas + Indiana sweep

These can be reordered by Chris if business priorities pull a specific market forward (e.g., Maryland, NC, VA from Chris's original brief).

## Off-Limits Rule

Built greens (the six scrapers in the "off-limits" column above) are reference material only. Builders read the existing scraper + matrix entry to learn the state's pattern, but do not modify them.

**Exception:** If a builder identifies a material improvement (more fields, higher hit rate, broader date range), they can submit an `Upgrade-proposed` row in the Sheet. Lead approves; only then is the upgrade either assigned back to the builder or absorbed by the lead. Chris has confirmed upgrades are allowed via this path.

## Two-Stage Approval Gate

### Stage 1 — Classification approval (pre-build)

Builder submits a Sheet row with:

- Live evidence (screenshot or probe script) showing the source is date-enumerable
- Estimated weekly filing volume from the live probe
- Drafted `source_discovery_matrix.md` entry matching the existing format

Lead approves `Green` (build authorized), or marks `Yellow` (hold for enrichment) or `Red` (skip). **No code is written before classification is approved.** This kills wasted builds at the source.

### Stage 2 — Build approval (pre-Live)

Seven checks before a scraper transitions from `Submitted-for-review` to `Live`:

1. `scripts/smoke_scrapers.py` returns ≥ 50 filings over a sensible lookback window
2. Output dict matches the existing scraper contract: `case_number`, `filing_date`, `plaintiff`, `defendant`, `defendant_address`, `source_url`, `county`, `state`
3. Pagination confirmed via multi-page lookback test
4. No crash on empty results, network timeout, or malformed page
5. **Address hit rate measured and reported — hard floor ≥ 60%.** Below that, the source stays Yellow and does not enter the pipeline.
6. `--yes-write-supabase --lookback-days 2` confirms Supabase insert/dedupe
7. Lead reviews the PR diff on GitHub

### Pipeline wiring is a separate approval

Turning on `--pipe` (SearchBug enrichment, GHL push, DNC scrub) is a per-scraper cost decision made deliberately after the build gate. SearchBug is the confirmed primary enrichment vendor for the tenant track (Melissa Personator is not licensed).

## Daily Reporting — Discovery Pipeline Sheet

Single shared Google Sheet, one row per county per builder.

### Columns

| Column | Allowed values / rule |
|---|---|
| Builder | Nourul / Franz / Donnald |
| State | Two-letter code |
| County / Source | County name + court level |
| Source URL | Live link to the portal |
| Stage | Researching → Classified-pending-approval → Approved-to-build → Building → Submitted-for-review → Live (terminal: Rejected, Skipped, Upgrade-proposed) |
| Classification | Green / Yellow / Red / TBD |
| Date-enumerable? | Y / N / TBD |
| Tenant name exposed? | Y / N / TBD |
| Property/defendant address exposed? | Y / N / TBD |
| Est. weekly filings | Number from live probe |
| Evidence | Link to screenshot / probe script / sample CSV / Loom |
| Blocker | Free text. Empty = no blocker. |
| Last updated | YYYY-MM-DD. Builders touch daily even if unchanged. |
| Lead notes | Lead-only column |
| PR link | GitHub PR URL once stage = Submitted-for-review |

### Conditional formatting

- `Last updated` > 2 days old AND stage ∉ {Live, Rejected, Skipped} → red cell (stuck)
- Stage = `Classified-pending-approval` OR `Submitted-for-review` → yellow row (lead's queue)
- Stage = `Live` → green row
- Classification = Red AND stage = Skipped → gray row

### Pinned filter views

- **My queue** — Stage ∈ {Classified-pending-approval, Submitted-for-review}. Lead's morning checklist.
- **Stuck** — Last updated > 2 days AND stage ∉ {Live, Rejected, Skipped}. Lead's escalation list.

### Code workflow — GitHub

All scraper code lands via GitHub pull requests. Sheet tracks the human workflow; GitHub tracks the code workflow. The two stages cross-reference: PR link goes in the Sheet's `PR link` column; PR title includes the county name.

## Rotation Rule

A state is "done" by lead's call, informed by daily Sheet updates. No formal coverage or volume threshold. Lead rotates a builder to the backlog state when the active queue is exhausted and the address-hit-rate floor is failing on remaining yellow counties.

## Lorraine and Recca Handoff

- **Lorraine** — One-day handoff. Yesterday's research findings go into the Sheet today as rows assigned to the relevant builder by state. After that drop, Lorraine moves to ShieldDesk research full-time (OSHA, NLRB, EEOC, SBA defaults, BBB).
- **Recca** — Starts ShieldDesk Track 2 immediately. Same five government agency sources, building the lead pipeline for businesses launching after ITTS.

## State Scope Picture

**Active and assigned:** Texas (Nourul), Ohio (Franz), Tennessee (Donnald).

**Backlog, queued:** Arizona, California, Kansas + Indiana.

**Greens already built and live:** Texas (Harris, Tarrant), Ohio (Hamilton, Franklin), Tennessee (Davidson).

**Yellow with scraper built but not pipelined:** Nevada (Clark Justice Court — scraper live, enrichment hit rate ~20–25%, awaiting assessor-match proof before pipeline).

**Assessed yellow, not yet assigned:** Georgia (Cobb, DeKalb), Nevada (Washoe), Wisconsin (statewide — blocked by hCaptcha until CCAP subscription or hCaptcha-bypass vendor approved).

**Assessed red, permanently skipped:** South Carolina (statewide address removal 2026-01-01), Florida (paid APIs only), Washington (RCW commercial-use restriction), Illinois Cook (bulk-data approval required), Indiana Marion (MyCase blocked), Arkansas (name-required), California Riverside (credit-gated), Georgia Fulton + Gwinnett (registration walls post-2025).

**Unmapped or under-researched:** Missouri, Minnesota, Michigan, Pennsylvania, Colorado, Connecticut, Virginia, Maryland, North Carolina, New Jersey, Oregon, Idaho.

## Open Items / Awaiting Chris

- Sign-off on the structure (email sent 2026-05-27, approved).
- Backlog-priority call: confirm whether to push next states by my recommendation (AZ, CA, KS+IN) or reorder for business priorities (MD, NC, VA from Chris's original brief).

## Out of Scope for This Spec

- ShieldDesk employer-records pipeline (NLRB, EEOC, OSHA, SBA, BBB) — separate brainstorm + spec.
- BatchData property-owner sources for Track 1 (Recca) — separate spec.
