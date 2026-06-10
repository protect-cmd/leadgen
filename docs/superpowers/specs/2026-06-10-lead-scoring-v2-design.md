# Lead Scoring v2 + Rent-in-Pipeline — Design

**Date:** 2026-06-10
**Status:** Approved (design)
**Supersedes:** `pipeline/lead_score.py` v1 (match 40 / coverage 35 / freshness 25)

## Why

v1 scored only *reachability* (name match, DNC-coverage, freshness) and ignored **rent**,
the #1 value signal. And DNCScrub (national, all area codes) made the **coverage factor
obsolete** — there's no "held" bucket anymore and the real DNC rate is a uniform ~15%, so
coverage-by-county barely predicts anything. Drop it, give the weight to rent.

## Decisions (approved)

- **Rent = linear, both businesses, top factor** (higher = better; no sweet-spot band).
- **Court date is NOT a score factor** — it stays a hard gate (future-or-null), but its data
  is too inconsistent (Davidson/Hamilton sentinel, Franklin null) to score on.
- **Coverage factor removed** (DNCScrub obsoletes it).
- Both businesses use the **same factors**; only the freshness source + window differ.

## TOP PRIORITY — Rent estimation strategy (Rentometer is limited)

Spend Rentometer calls in this order; do NOT backfill the full ~9,600 backlog.

1. **Ingest-time (going forward):** `rent_estimate_service.estimate_rent` already runs in the
   runner's rent-precheck but **discards** the result. **Persist it to `filings.estimated_rent`**
   and set `RENT_PRECHECK_ENABLED=true` → every new scrape auto-carries rent, zero extra calls.
2. **Existing scored leads (one-time, scoped, capped/day):** only the leads we actually score,
   priority-first:
   - **Vantage:** `good_leads_now WHERE estimated_rent IS NULL ORDER BY priority_rank NULLS LAST, filing_date DESC LIMIT <daily-cap>`
   - **ISTS:** `build_ists_to_enrich` judgments missing rent, same ordering.
   - Run daily until drained; never the 9,600 backlog in one shot.

## Pipeline integration (neither is fully automatic on scrapes today)

- **Rent:** persist `rent_estimate_service` output to `estimated_rent`; add a Rentometer lookup
  to the ISTS enrich path (judgments have no rent today); unify on `RENTOMETER_API_KEY`
  (standalone scripts currently hardcode the key).
- **DNC:** gated at enrich-time + Fire button + ISTS dial, but the **runner's auto-Bland
  (`runner.py:224`) is ungated**. Move the DNC gate into `bland_service.trigger_voicemail`
  (the single chokepoint) so every dial path — including the auto-runner — scrubs. Then the
  per-path gates become redundant belt-and-suspenders.

## Score model — `pipeline/lead_score.py` v2

```
score_lead(*, rent, tenant_name, lead_date, today, business, fresh_window_days):
    rent_pts  = clamp((rent - 800) / (3500 - 800), 0, 1) * 50   # value (top)
    match_pts = 30 * (1.0 if uncommon_surname else 0.55)        # reachability
    fresh_pts = 20 * clamp((fresh_window_days - age_days) / fresh_window_days, 0, 1)
    return round(rent_pts + match_pts + fresh_pts)              # 0-100
```

- **rent missing → rent_pts = 0** (lead still scores on match+fresh, max 50, so it sorts below
  rent-known leads — which nudges us to estimate its rent next).
- **Vantage:** `lead_date = filing_date`, window 21. **ISTS:** `lead_date = judgment_date`, window 7.
- Weights identical across businesses (per the approved decision); the only per-business
  difference is the date source + window.
- **Coverage removed.** The priority-ZIP tier stays the primary sort in the builders
  (`ORDER BY priority_rank NULLS LAST, score DESC`); rent-in-score orders within tiers + the tail.

## Phases (implementation)

1. **Score v2** — rewrite `lead_score.score_lead` (rent/match/freshness, drop coverage) + tests;
   wire rent through the builders (`build_to_enrich`/`to_fire`/`ists_*` pass `estimated_rent`).
2. **Rent in pipeline** — persist precheck rent to `estimated_rent`; ISTS Rentometer lookup;
   scoped priority-first daily backfill script.
3. **DNC chokepoint** — gate `bland_service.trigger_voicemail` (closes the runner gap).
4. **Verify** — score distribution, queue ordering with rent, To-Fire compliance.

## Out of scope
- ML scoring (rules only — interpretable, tunable; revisit if it ever plateaus).
- Sweet-spot rent bands (linear per decision).
