# Lead-Quality Flag — Design

**Date:** 2026-06-10
**Status:** Approved

## Problem

Every lead-selection script (`select_100_filing_basis.py`, `rentometer_rank_enrichable.py`,
`enrich_stage_bland.py`, …) re-derives "is this a good, enrichable lead?" from scratch
each run — ~40 lines of gate logic, a `lead_contacts` dedup query, and an entity/name
filter. There is no persisted notion of "this filing passed the gates." We want a durable
identifier so a good lead is a single query, not a re-computation.

## Key insight: static vs. dynamic gates

The enrichment gates split into two kinds:

| Kind | Gates | Changes over time? |
|---|---|---|
| **Static** | `residential_approved` bucket, clean person name (`gate_name`), valid address (`gate_address`) | No — fixed at ingest |
| **Dynamic** | filing freshness, court date not passed, not-yet-phoned | Yes — depend on *today* / external state |

A persisted boolean for the **static** gates never goes stale. Baking the **dynamic**
gates into a stored flag would require a daily refresh job. So we persist only the static
half and compute the dynamic half live.

## Design (Option C: static column + live view)

### 1. Schema — migration `017_lead_quality.sql`

```sql
ALTER TABLE filings ADD COLUMN IF NOT EXISTS is_enrichable BOOLEAN;
ALTER TABLE filings ADD COLUMN IF NOT EXISTS enrichable_checked_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_filings_enrichable ON filings (is_enrichable) WHERE is_enrichable;

CREATE OR REPLACE VIEW good_leads_now AS
SELECT f.*
FROM filings f
WHERE f.is_enrichable = TRUE
  AND (f.court_date IS NULL OR f.court_date >= CURRENT_DATE)
  AND NOT EXISTS (
    SELECT 1 FROM lead_contacts lc
    WHERE lc.case_number = f.case_number
      AND lc.track = 'ng' AND lc.phone IS NOT NULL
  );
```

`is_enrichable` is nullable so an un-flagged filing is distinguishable from a flagged-false
one. Additive + nullable → safe to apply on the live system.

### 2. `is_enrichable` definition (static gates only)

```
is_enrichable = (lead_bucket == 'residential_approved')
                AND gate_name(tenant_name)        # clean person, non-entity, parseable
                AND gate_address(property_address) # street# + STATE ZIP present
```

Reuses the existing `pipeline.gates` functions verbatim — single source of truth, no SQL
re-implementation of the name/entity regex (which would risk divergence).

### 3. Population — `scripts/flag_enrichable.py`

- Importable `flag(case_numbers=None, only_null=False)` + CLI.
- Backfill: paginate all filings, compute `is_enrichable`, bulk-update in chunks of 200
  (two grouped updates — true-set and false-set — not per-row).
- Idempotent: static inputs don't change, so re-runs are safe; `--only-null` skips
  already-flagged rows for incremental refresh.
- Scraper hook: a scraper passes its new `case_numbers` to `flag([...])` at end of run so
  fresh filings get flagged without a cron. (Wiring into individual scrapers is a
  follow-up; the backfill CLI covers the gap meanwhile.)

### 4. Freshness stays at query time

The filing-age window is the knob operators keep changing (6 / 14 / 30 days), so it is
**not** baked into the column or view. Selection collapses to:

```sql
SELECT * FROM good_leads_now WHERE filing_date >= CURRENT_DATE - 14;
```

## Out of scope (YAGNI)

- No scoring/ranking in the flag (Rentometer ranking stays a separate step).
- No automatic re-flagging cron (backfill CLI + scraper hook suffice).
- No rewrite of existing selection scripts in this phase — they keep working; migrating
  them to `good_leads_now` is a follow-up cleanup.

## Manual step

DDL is applied via the Supabase SQL editor (no direct Postgres URL in env, same as prior
migrations). After the migration is applied, run `python scripts/flag_enrichable.py` to
backfill.
