# Unified Lead Qualification & Enrichment — Design

**Date:** 2026-06-10
**Status:** Approved (design); phased implementation
**Supersedes selection logic in:** `select_*`, `rentometer_rank_enrichable`, ad-hoc gates in `enrich_stage_bland`

## Business goal

Drive revenue from two tenant-facing businesses by spending SearchBug only on
**quality, high-value, callable** leads — and identifying them cleanly so the best
phone numbers get fired for outreach:

- **Vantage (NG):** eviction *filings* — reach the tenant *before* court to sell defense.
- **ISTS:** *judgments* (tenant already lost) — reach them *after* judgment.

Same tenants, same metros — so they share a quality bar and an engine, but stay
separate inventories with separate offers.

## Locked decisions

| Decision | Choice |
|---|---|
| ZIP allowlist (Vantage) | **Drop** — was landlord-calibrated; discards ~4,562 (42%) tenant leads |
| Rent threshold | **Not a gate — a priority score.** Nothing discarded on rent; high-rent enriches/outreach first |
| Architecture | **Separate source tables, one shared engine**, track-tagged outreach |
| ISTS freshness | **7 days** (both scrape window and enrichment) |
| Cross-track precedence | **ISTS wins** when the same person is on both |
| Match discipline | **Strict, but keep common surnames**; self-heal missing ZIPs |
| Rentometer + DNC | Run for **both** tracks |
| DNCScrub.com API | Parked — researched separately, decided later |

## Architecture

```
filings ─────────┐
                 ├─► readiness view ─► priority score ─► ENRICH ENGINE ─► GHL + Bland
ists_judgments ──┘                                          │            (track-tagged)
                                                            └─ persist DNC tag: callable | held | on_dnc
```

Two source tables stay distinct (no data mixing). One parameterized engine handles
readiness → scoring → SearchBug → DNC-tag → track-tagged outreach. Cross-track dedup
by **person-key** (normalized name + property ZIP); ISTS wins.

## Qualification model (Vantage `classify_lead` rework)

Dropping the ZIP and rent gates collapses classification to quality + type only:

1. **Self-heal address/ZIP** (new): if ZIP missing, recover from street+city+state via
   the free **US Census batch geocoder**; persist the healed address.
2. **Commercial** type → `commercial` bucket (unchanged).
3. Else → `residential_approved`.
4. **Freshness is decoupled from classification.** Remove the 7-day `held` bucketing;
   age becomes a query-time filter + score input, not a classification fork. (Flags the
   prior "Chris review at 7 days" workflow for removal — confirm.)

Net effect: the ~4,562 `zip_not_approved` + 885 `rent_below_threshold` rejoin the
enrichable pool; rent re-enters as a score.

## Readiness layer (both tracks)

- **Vantage:** `filings.is_enrichable` = `residential_approved` AND `gate_name` AND
  `gate_address` (post self-heal). View `good_leads_now` = `is_enrichable` + court-future
  + not-phoned + not-worked-on-ISTS. Caller adds freshness window.
- **ISTS:** `ists_judgments.is_enrichable` = tenant-lost AND `gate_name` AND `gate_address`.
  View `good_judgments_now` = `is_enrichable` + `judgment_date >= today-7` + not-phoned.

## Priority score (spend SearchBug smart)

Per lead, computed before enrichment; enrich highest-first within budget:

```
score = w_value      * Rentometer_rent (both tracks)
      + w_match       * match_likelihood   (has street+ZIP after heal; address completeness)
      + w_coverage    * dnc_coverage_likelihood (historical: does this metro yield covered area codes?)
```

`dnc_coverage_likelihood` is learned from the ~1,063 already-enriched phones (covered vs
out-of-market rate per county/ZIP). High-rent + high-coverage + high-match enrich first.

## Enrichment engine (shared, parameterized by track)

For each lead, highest score first:
1. SearchBug `search_tenant_detailed` — strict address (street+ZIP, self-healed), **common
   surnames kept** (a clean street+ZIP disambiguates them).
2. Persist phone + `language_hint`.
3. **DNC tag** (persisted column): `callable` (scrubbed clean) | `on_dnc` (drop) |
   `held` (area code not in our DNC scope — flagged out-of-scope, never re-fired).
4. `callable` → stage GHL (track tag: `Vantage` / `ISTS`) + Bland (right agent) →
   set `bland_status`.
5. **Cross-track dedup:** before staging, check the other track by person-key; if the
   person is being worked on ISTS, suppress the Vantage offer (ISTS wins).

ISTS additions vs today: Rentometer ranking + DNC scrub + the readiness view (it currently
does neither).

## Phasing (revenue-first)

| Phase | Scope | Outcome |
|---|---|---|
| **1** | Drop ZIP gate + rent→score + decouple freshness; re-classify `filings`; re-run `flag_enrichable` | **~5,400-lead pool unlock**, immediate |
| 2 | Self-healing ZIP recovery (Census geocoder) | salvage + sharper matches |
| 3 | ISTS readiness parity: `is_enrichable` + `good_judgments_now` (7-day) | ISTS on the abstraction |
| 4 | Shared enrich engine: score → SearchBug → DNC-tag → track-tagged GHL/Bland; ISTS-wins dedup; Rentometer both | the unification |
| 5 | DNC-coverage scoring + DNCScrub.com research | fine-tune spend |

## Out of scope / parked

- DNCScrub.com API integration (research first, separate decision).
- Real-time DNC API for full area-code coverage (depends on DNCScrub.com research).
- Rewriting every legacy `select_*` script — they keep working; migrate to the views over time.

## Open confirmations

1. Removing the 7-day `held` "Chris review" bucket (freshness becomes query-time only).
2. Census geocoder as the ZIP-recovery backend (free, US-only, no key) vs a paid geocoder.
